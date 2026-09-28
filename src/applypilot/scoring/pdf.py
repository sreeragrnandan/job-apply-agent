"""Text-to-PDF conversion for tailored resumes and cover letters.

Parses the structured text resume format, renders via an HTML/CSS template,
and exports to PDF using headless Chromium via Playwright.
"""

import html as html_module
import logging
import re
from datetime import datetime
from pathlib import Path

from applypilot.config import COVER_LETTER_DIR, TAILORED_DIR, load_profile

log = logging.getLogger(__name__)


# ── Resume Parser ────────────────────────────────────────────────────────

def parse_resume(text: str) -> dict:
    """Parse a structured text resume into sections.

    Expects a format with header lines (name, title, location, contact)
    followed by ALL-CAPS section headers (SUMMARY, TECHNICAL SKILLS, etc.).

    Args:
        text: Full resume text.

    Returns:
        {"name": str, "title": str, "location": str, "contact": str, "sections": dict}
    """
    lines = [line.rstrip() for line in text.strip().split("\n")]

    # Header: first few lines before SUMMARY
    header_lines: list[str] = []
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip().upper() == "SUMMARY":
            body_start = i
            break
        if line.strip():
            header_lines.append(line.strip())

    name = header_lines[0] if len(header_lines) > 0 else ""
    title = header_lines[1] if len(header_lines) > 1 else ""
    # The header may have 3 or 4 lines depending on whether location is included
    location = ""
    contact = ""
    if len(header_lines) > 3:
        location = header_lines[2]
        contact = header_lines[3]
    elif len(header_lines) > 2:
        # Could be location or contact -- check for email/phone indicators
        if "@" in header_lines[2] or "|" in header_lines[2]:
            contact = header_lines[2]
        else:
            location = header_lines[2]

    # Split body into sections by ALL-CAPS headers
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    for line in lines[body_start:]:
        stripped = line.strip()
        # Detect section headers (all caps, no leading dash/bullet, longer than 3 chars)
        if (
            stripped
            and stripped == stripped.upper()
            and not stripped.startswith("-")
            and len(stripped) > 3
            and not stripped.startswith("\u2022")
        ):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return {
        "name": name,
        "title": title,
        "location": location,
        "contact": contact,
        "sections": sections,
    }


def parse_skills(text: str) -> list[tuple[str, str]]:
    """Parse skills section into (category, value) pairs.

    Args:
        text: The TECHNICAL SKILLS section text.

    Returns:
        List of (category_name, skills_string) tuples.
    """
    skills: list[tuple[str, str]] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if ":" in line:
            cat, val = line.split(":", 1)
            skills.append((cat.strip(), val.strip()))
    return skills


def parse_entries(text: str) -> list[dict]:
    """Parse experience/project entries from section text.

    Args:
        text: The EXPERIENCE or PROJECTS section text.

    Returns:
        List of {"title": str, "subtitle": str, "bullets": list[str]} dicts.
    """
    entries: list[dict] = []
    lines = text.strip().split("\n")
    current: dict | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") or stripped.startswith("\u2022 "):
            if current:
                current["bullets"].append(stripped[2:].strip())
        elif current is None or (
            not stripped.startswith("-")
            and not stripped.startswith("\u2022")
            and len(current.get("bullets", [])) > 0
        ):
            # New entry
            if current:
                entries.append(current)
            current = {"title": stripped, "subtitle": "", "bullets": []}
        elif current and not current["subtitle"]:
            current["subtitle"] = stripped
        else:
            if current:
                current["bullets"].append(stripped)

    if current:
        entries.append(current)

    return entries


_URL_REGEX = re.compile(
    r'(?P<url>'
    r'https?://[^\s<>"\'\)]+'
    r'|www\.[^\s<>"\'\)]+'
    r'|(?:youtu\.be|github\.com|linkedin\.com|bit\.ly|[a-zA-Z0-9.-]+\.github\.io)/[^\s<>"\'\)]*'
    r'|[a-zA-Z0-9.-]+\.github\.io(?:#[^\s<>"\'\)]*)?'
    r')'
)


def linkify_text(text: str) -> str:
    """Escape HTML and wrap detected URLs with clickable <a href="..."> tags."""
    if not text:
        return ""
    parts = []
    last_end = 0
    for m in _URL_REGEX.finditer(text):
        start, end = m.span()
        parts.append(html_module.escape(text[last_end:start]))
        raw_url = m.group("url")
        trailing = ""
        while raw_url and raw_url[-1] in ".,;:)":
            trailing = raw_url[-1] + trailing
            raw_url = raw_url[:-1]
        href = raw_url
        if not href.startswith(("http://", "https://", "mailto:")):
            href = f"https://{href}"
        escaped_href = html_module.escape(href)
        escaped_text = html_module.escape(raw_url)
        parts.append(f'<a href="{escaped_href}" target="_blank">{escaped_text}</a>{trailing}')
        last_end = end
    parts.append(html_module.escape(text[last_end:]))
    return "".join(parts)


def parse_education(text: str) -> dict:
    """Parse education section into degree, dates, and details for a 2-line layout."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    date_pattern = re.compile(
        r'(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}|\d{4})\s*[-–—]\s*(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}|\d{4}|Present)',
        re.I,
    )
    degree = ""
    dates = ""
    details = ""

    if len(lines) >= 3:
        degree = lines[0]
        if date_pattern.search(lines[1]):
            dates = lines[1]
            details = " ".join(lines[2:])
        else:
            details = " ".join(lines[1:])
    elif len(lines) == 2:
        degree = lines[0]
        if date_pattern.search(lines[1]):
            dates = lines[1]
        else:
            details = lines[1]
    elif len(lines) == 1:
        parts = re.split(r',\s*(?=CGPA)', lines[0], maxsplit=1)
        if len(parts) == 2:
            degree = parts[0]
            details = parts[1]
        else:
            degree = lines[0]

    if not dates:
        dates = "July 2017 – March 2021"

    return {"degree": degree, "dates": dates, "details": details}


# ── HTML Template ────────────────────────────────────────────────────────

def build_html(resume: dict) -> str:
    """Build professional resume HTML from parsed data.

    Args:
        resume: Parsed resume dict from parse_resume().

    Returns:
        Complete HTML string ready for PDF rendering.
    """
    sections = resume["sections"]

    # Skills
    skills_html = ""
    if "TECHNICAL SKILLS" in sections:
        skills = parse_skills(sections["TECHNICAL SKILLS"])
        rows = ""
        for cat, val in skills:
            rows += f'<div class="skill-row"><span class="skill-cat">{cat}:</span> {linkify_text(val)}</div>\n'
        skills_html = f'<div class="section"><div class="section-title">Technical Skills</div>{rows}</div>'

    # Experience
    exp_html = ""
    if "EXPERIENCE" in sections:
        entries = parse_entries(sections["EXPERIENCE"])
        items = ""
        for e in entries:
            bullets = "".join(f"<li>{linkify_text(b)}</li>" for b in e["bullets"])
            subtitle = f'<div class="entry-subtitle">{linkify_text(e["subtitle"])}</div>' if e["subtitle"] else ""
            items += f'<div class="entry"><div class="entry-title">{linkify_text(e["title"])}</div>{subtitle}<ul>{bullets}</ul></div>'
        exp_html = f'<div class="section"><div class="section-title">Experience</div>{items}</div>'

    # Projects
    proj_html = ""
    if "PROJECTS" in sections:
        entries = parse_entries(sections["PROJECTS"])
        items = ""
        for e in entries:
            bullets = "".join(f"<li>{linkify_text(b)}</li>" for b in e["bullets"])
            subtitle = f'<div class="entry-subtitle">{linkify_text(e["subtitle"])}</div>' if e["subtitle"] else ""
            items += f'<div class="entry"><div class="entry-title">{linkify_text(e["title"])}</div>{subtitle}<ul>{bullets}</ul></div>'
        proj_html = f'<div class="section"><div class="section-title">Projects</div>{items}</div>'

    # Honors
    honors_html = ""
    if "HONORS" in sections:
        honors_text = sections["HONORS"].strip()
        lines = [l.strip() for l in honors_text.split("\n") if l.strip()]
        b_items = "".join(f"<li>{linkify_text(l.lstrip('- '))}</li>" for l in lines)
        honors_html = f'<div class="section"><div class="section-title">Honors & Awards</div><ul>{b_items}</ul></div>'

    # Education (2-line layout: Degree on left [bold] + Dates on right; CGPA and school on line 2)
    edu_html = ""
    if "EDUCATION" in sections:
        edu_text = sections["EDUCATION"].strip()
        edu = parse_education(edu_text)
        edu_html = (
            f'<div class="section"><div class="section-title">Education</div>'
            f'<div class="edu-row">'
            f'<span class="edu-degree">{linkify_text(edu["degree"])}</span>'
            f'<span class="edu-date">{linkify_text(edu["dates"])}</span>'
            f'</div>'
            f'<div class="edu-details">{linkify_text(edu["details"])}</div>'
            f'</div>'
        )

    # Summary
    summary_html = ""
    if "SUMMARY" in sections:
        summary_html = f'<div class="section"><div class="section-title">Summary</div><div class="summary">{linkify_text(sections["SUMMARY"].strip())}</div></div>'

    # Contact line parsing
    contact = resume["contact"]
    contact_parts = []
    if contact:
        for p in contact.split("|"):
            p = p.strip()
            if not p:
                continue
            if "@" in p and not p.startswith("http") and "/" not in p:
                contact_parts.append(f'<a href="mailto:{html_module.escape(p)}">{html_module.escape(p)}</a>')
            else:
                contact_parts.append(linkify_text(p))
    contact_html = " &nbsp;|&nbsp; ".join(contact_parts)

    # Location line (may be empty)
    location_html = f'<div class="location">{resume["location"]}</div>' if resume["location"] else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{
    size: letter;
    margin: 0.22in 0.45in;
}}
* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}}
body {{
    font-family: 'Calibri', 'Segoe UI', Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.35;
    color: #1a1a1a;
}}
a {{
    color: #2a7ab5;
    text-decoration: none;
}}
a:hover {{
    text-decoration: underline;
}}
.header {{
    text-align: center;
    margin-bottom: 4px;
    padding-bottom: 4px;
    border-bottom: 1.5px solid #2a7ab5;
}}
.name {{
    font-size: 18pt;
    font-weight: 700;
    color: #1a3a5c;
    letter-spacing: 0.5px;
}}
.title {{
    font-size: 10.5pt;
    color: #3a6b8c;
    margin: 1px 0;
}}
.location {{
    font-size: 9pt;
    color: #555;
}}
.contact {{
    font-size: 9pt;
    color: #444;
    margin-top: 1px;
}}
.contact a {{
    color: #2c3e50;
    text-decoration: none;
}}
.entry-title a {{
    color: #2a7ab5;
    text-decoration: none;
}}
.entry-title a:hover {{
    text-decoration: underline;
}}
.section {{
    margin-top: 5px;
}}
.section-title {{
    font-size: 10pt;
    font-weight: 700;
    color: #1a3a5c;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    border-bottom: 1.5px solid #2a7ab5;
    padding-bottom: 1px;
    margin-bottom: 3px;
}}
.summary {{
    font-size: 9.5pt;
    color: #333;
    line-height: 1.4;
}}
.skill-row {{
    font-size: 9.5pt;
    margin: 0;
    line-height: 1.35;
}}
.skill-cat {{
    font-weight: 600;
    color: #1a3a5c;
}}
.entry {{
    margin-bottom: 4px;
    break-inside: avoid;
}}
.entry-title {{
    font-weight: 600;
    font-size: 10pt;
    color: #1a3a5c;
}}
.entry-subtitle {{
    font-size: 9pt;
    color: #4a7a9b;
    font-style: italic;
    margin-bottom: 1px;
}}
ul {{
    margin-left: 14px;
    padding: 0;
}}
li {{
    font-size: 9.5pt;
    margin-bottom: 1px;
    line-height: 1.35;
}}
.edu-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 9.5pt;
    margin-bottom: 1px;
}}
.edu-degree {{
    font-weight: 700;
    color: #1a1a1a;
}}
.edu-date {{
    font-size: 9pt;
    color: #333333;
    text-align: right;
    white-space: nowrap;
}}
.edu-details {{
    font-size: 9.5pt;
    color: #1a1a1a;
    line-height: 1.35;
}}
</style>
</head>
<body>
<div class="header">
    <div class="name">{resume['name']}</div>
    <div class="title">{resume['title']}</div>
    {location_html}
    <div class="contact">{contact_html}</div>
</div>
{summary_html}
{skills_html}
{exp_html}
{proj_html}
{honors_html}
{edu_html}
</body>
</html>"""


# ── PDF Renderer ─────────────────────────────────────────────────────────

def count_pdf_pages(pdf_path: str | Path) -> int:
    """Read PDF binary to count total pages."""
    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
        counts = re.findall(rb'/Count\s+(\d+)', data)
        if counts:
            try:
                return int(counts[0])
            except ValueError:
                pass
        pages = re.findall(rb'/Type\s*/Page\b', data)
        return len(pages) or 1
    except Exception:
        return 1


def render_pdf(
    html: str,
    output_path: str,
    page=None,
    ensure_single_page: bool = True,
) -> None:
    """Render HTML to PDF using Playwright's headless Chromium.

    Args:
        html: Complete HTML string.
        output_path: Path to write the PDF file.
        page: Optional existing Playwright Page object.
        ensure_single_page: If True (default), dynamically auto-scales down in tiny
            increments (1.0 -> 0.97 -> 0.94 -> 0.91 -> 0.88 -> 0.85) if content
            expands, ensuring the document stays on exactly 1 page.
    """
    scales = [1.0, 0.97, 0.94, 0.91, 0.88, 0.85] if ensure_single_page else [1.0]

    def _render_with_page(p):
        p.set_content(html, wait_until="networkidle")
        for s in scales:
            p.pdf(
                path=output_path,
                format="Letter",
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                print_background=True,
                scale=s,
            )
            if not ensure_single_page or count_pdf_pages(output_path) == 1:
                if s < 1.0:
                    log.info("Auto-scaled PDF to %.2f to fit on exactly 1 page: %s", s, output_path)
                return

    if page:
        _render_with_page(page)
        return

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page_obj = browser.new_page()
        _render_with_page(page_obj)
        browser.close()


# ── Cover Letter Parser & Template ──────────────────────────────────────

def is_cover_letter(text_path: Path | str, text: str = "") -> bool:
    """Determine whether a file or text content is a cover letter.

    Checks file path naming patterns and content markers.
    """
    path_str = str(text_path).lower()
    if "_cl." in path_str or "cover_letter" in path_str or "coverletter" in path_str:
        return True

    if not text and Path(text_path).exists():
        try:
            text = Path(text_path).read_text(encoding="utf-8")
        except Exception:
            pass

    stripped = text.strip()
    if stripped.lower().startswith("dear "):
        upper_text = stripped.upper()
        if "TECHNICAL SKILLS" not in upper_text and "EXPERIENCE" not in upper_text:
            return True

    return False


def parse_cover_letter(
    text: str, profile: dict | None = None, file_path: Path | None = None
) -> dict:
    """Parse cover letter text and integrate candidate profile information.

    Args:
        text: Raw text of the cover letter.
        profile: Candidate profile dictionary (loaded from disk if omitted).
        file_path: Optional path to the text file (used for file modification date).

    Returns:
        Structured dictionary for cover letter HTML rendering.
    """
    if profile is None:
        try:
            profile = load_profile()
        except Exception:
            profile = {}

    personal = profile.get("personal", {})
    experience = profile.get("experience", {})

    full_name = personal.get("full_name") or "Sreerag R Nandan"
    title = (
        experience.get("current_title")
        or personal.get("title")
        or profile.get("target_role")
        or "Senior Software Engineer"
    )

    # Phone formatting
    phone = personal.get("phone", "")
    if phone:
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 10 and not phone.startswith("+"):
            country = personal.get("country", "").lower()
            if country in ("india", "in", ""):
                phone = f"+91{digits}"
            else:
                phone = f"+1{digits}"
        elif not phone.startswith("+"):
            phone = f"+{phone}"
    else:
        phone = "+917034274990"

    email = personal.get("email") or "sreeragnandan25@gmail.com"

    linkedin_url = personal.get("linkedin_url", "") or "https://www.linkedin.com/in/srnofficial"
    linkedin_display = re.sub(r"^https?://(www\.)?", "", linkedin_url).rstrip("/")

    # Date extraction: check text for 'Date: ...'
    date_str = ""
    date_match = re.search(r"Date:\s*([^\n]+)", text, re.IGNORECASE)
    if date_match:
        date_str = date_match.group(1).strip()
    elif file_path and Path(file_path).exists():
        date_str = datetime.fromtimestamp(Path(file_path).stat().st_mtime).strftime("%B %d, %Y")
    else:
        date_str = datetime.now().strftime("%B %d, %Y")

    # Split lines and extract salutation
    lines = [l.strip() for l in text.strip().splitlines()]
    salutation = "Dear Hiring Manager,"
    salutation_idx = -1
    for i, line in enumerate(lines):
        if line.lower().startswith("dear "):
            salutation = line
            salutation_idx = i
            break

    content_lines = lines[salutation_idx + 1 :] if salutation_idx != -1 else lines

    # Group content lines into paragraphs
    raw_paragraphs: list[str] = []
    current_p: list[str] = []
    for l in content_lines:
        if not l:
            if current_p:
                raw_paragraphs.append(" ".join(current_p))
                current_p = []
        else:
            current_p.append(l)
    if current_p:
        raw_paragraphs.append(" ".join(current_p))

    # Strip closing/sign-off from paragraphs
    body_paragraphs: list[str] = []
    signoff_tokens = {
        "yours faithfully",
        "yours sincerely",
        "sincerely",
        "regards",
        "best regards",
        "warm regards",
        full_name.lower(),
    }
    preferred_name = personal.get("preferred_name", "").lower()
    if preferred_name:
        signoff_tokens.add(preferred_name)

    for i, para in enumerate(raw_paragraphs):
        para_clean = para.strip().rstrip(",.").lower()
        if i >= len(raw_paragraphs) - 2 and (
            para_clean in signoff_tokens
            or any(para_clean.startswith(tok) for tok in ["yours faithfully", "yours sincerely", "sincerely", "regards"])
        ):
            continue
        body_paragraphs.append(para)

    return {
        "name": full_name,
        "title": title,
        "phone": phone,
        "email": email,
        "linkedin_url": linkedin_url,
        "linkedin_display": linkedin_display,
        "date": date_str,
        "salutation": salutation,
        "paragraphs": body_paragraphs,
        "closing": "Yours Faithfully",
        "sign_off_name": full_name,
    }


def build_cover_letter_html(data: dict) -> str:
    """Build LaTeX-style executive cover letter HTML from parsed data.

    Matches the exact layout, Source Sans font, royal blue palette (#204096),
    3-column header, divider, and typography from the executive template.

    Args:
        data: Parsed cover letter dictionary from parse_cover_letter().

    Returns:
        Complete HTML string ready for PDF rendering.
    """
    paragraphs_html = "\n".join(
        f"    <p>{linkify_text(p)}</p>" for p in data["paragraphs"]
    )

    date_val = data["date"]
    date_display = date_val if date_val.lower().startswith("date:") else f"Date: {date_val}"

    linkedin_html = ""
    if data.get("linkedin_display"):
        linkedin_html = (
            f'<a href="{html_module.escape(data["linkedin_url"])}" target="_blank">'
            f'{html_module.escape(data["linkedin_display"])}</a>'
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,300;0,400;0,600;0,700;1,400&display=swap');

@page {{
    size: letter;
    margin: 0.45in 0.5in;
}}

* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}}

body {{
    font-family: 'Source Sans 3', 'Source Sans Pro', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.4;
    color: #000000;
    background-color: #ffffff;
    -webkit-font-smoothing: antialiased;
    padding: 10px 0;
}}

.header-container {{
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    padding-bottom: 6px;
}}

.header-left {{
    flex: 1;
    text-align: left;
    font-size: 11.5pt;
    line-height: 1.4;
    color: #000000;
}}

.header-left a {{
    color: #000000;
    text-decoration: none;
}}

.header-center {{
    flex: 2;
    text-align: center;
}}

.header-name {{
    font-size: 26pt;
    font-weight: 700;
    color: #000000;
    line-height: 1.1;
    letter-spacing: -0.2px;
}}

.header-title {{
    font-size: 14pt;
    font-weight: 400;
    color: #204096;
    margin-top: 4px;
}}

.header-right {{
    flex: 1;
    text-align: right;
    font-size: 11.5pt;
    line-height: 1.4;
}}

.header-right a {{
    color: #000000;
    text-decoration: none;
}}

.divider-line {{
    border: none;
    border-top: 2px solid #204096;
    margin: 6px 0 34px 0;
    width: 100%;
}}

.doc-title {{
    text-align: center;
    font-size: 14pt;
    font-weight: 400;
    color: #204096;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-bottom: 30px;
}}

.meta-date {{
    font-size: 11pt;
    color: #000000;
    margin-bottom: 20px;
}}

.salutation {{
    font-size: 11pt;
    color: #000000;
    margin-bottom: 18px;
}}

.letter-content {{
    text-align: justify;
    text-justify: inter-word;
    font-size: 11pt;
    line-height: 1.42;
    color: #000000;
}}

.letter-content p {{
    margin-bottom: 18px;
}}

.signoff {{
    margin-top: 32px;
    font-size: 11pt;
    line-height: 1.4;
    color: #000000;
}}
</style>
</head>
<body>

<div class="header-container">
    <div class="header-left">
        <div>{html_module.escape(data['phone'])}</div>
        <div><a href="mailto:{html_module.escape(data['email'])}">{html_module.escape(data['email'])}</a></div>
    </div>
    <div class="header-center">
        <div class="header-name">{html_module.escape(data['name'])}</div>
        <div class="header-title">{html_module.escape(data['title'])}</div>
    </div>
    <div class="header-right">
        {linkedin_html}
    </div>
</div>

<hr class="divider-line">

<div class="doc-title">COVER LETTER</div>

<div class="meta-date">{html_module.escape(date_display)}</div>

<div class="salutation">{html_module.escape(data['salutation'])}</div>

<div class="letter-content">
{paragraphs_html}
</div>

<div class="signoff">
    <div>{html_module.escape(data['closing'])}</div>
    <div>{html_module.escape(data['sign_off_name'])}</div>
</div>

</body>
</html>"""


# ── Public API ───────────────────────────────────────────────────────────

def convert_cover_letter_to_pdf(
    text_path: Path,
    output_path: Path | None = None,
    html_only: bool = False,
    profile: dict | None = None,
    page=None,
) -> Path:
    """Convert a text cover letter to a PDF matching the executive LaTeX layout.

    Args:
        text_path: Path to the .txt cover letter.
        output_path: Optional override for the output PDF path.
        html_only: If True, writes HTML instead of PDF.
        profile: Candidate profile dict.
        page: Optional existing Playwright Page object.

    Returns:
        Path to the generated PDF (or HTML) file.
    """
    text_path = Path(text_path)
    text = text_path.read_text(encoding="utf-8")
    data = parse_cover_letter(text, profile=profile, file_path=text_path)
    html = build_cover_letter_html(data)

    if html_only:
        out = output_path or text_path.with_suffix(".html")
        out = Path(out)
        out.write_text(html, encoding="utf-8")
        log.info("Cover letter HTML generated: %s", out)
        return out

    out = output_path or text_path.with_suffix(".pdf")
    out = Path(out)
    render_pdf(html, str(out), page=page)
    log.info("Cover letter PDF generated: %s", out)
    return out


def convert_to_pdf(
    text_path: Path,
    output_path: Path | None = None,
    html_only: bool = False,
    page=None,
) -> Path:
    """Convert a text resume/cover letter to PDF.

    Automatically detects whether the file is a cover letter or resume.

    Args:
        text_path: Path to the .txt file to convert.
        output_path: Optional override for the output path. Defaults to same
            name with .pdf extension.
        html_only: If True, output HTML instead of PDF.
        page: Optional existing Playwright Page object.

    Returns:
        Path to the generated PDF (or HTML) file.
    """
    text_path = Path(text_path)
    text = text_path.read_text(encoding="utf-8")

    if is_cover_letter(text_path, text):
        return convert_cover_letter_to_pdf(
            text_path, output_path=output_path, html_only=html_only, page=page
        )

    resume = parse_resume(text)
    html = build_html(resume)

    if html_only:
        out = output_path or text_path.with_suffix(".html")
        out = Path(out)
        out.write_text(html, encoding="utf-8")
        log.info("HTML generated: %s", out)
        return out

    out = output_path or text_path.with_suffix(".pdf")
    out = Path(out)
    render_pdf(html, str(out), page=page)
    log.info("PDF generated: %s", out)
    return out


def batch_convert_cover_letters(limit: int = 50) -> int:
    """Convert .txt files in COVER_LETTER_DIR that don't have corresponding PDFs."""
    if not COVER_LETTER_DIR.exists():
        log.warning("Cover letter directory does not exist: %s", COVER_LETTER_DIR)
        return 0

    txt_files = sorted(COVER_LETTER_DIR.glob("*_CL.txt"))
    to_convert = [f for f in txt_files if not f.with_suffix(".pdf").exists()][:limit]

    if not to_convert:
        log.info("All cover letter text files already have PDFs.")
        return 0

    from playwright.sync_api import sync_playwright

    log.info("Converting %d cover letters to PDF...", len(to_convert))
    converted = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for f in to_convert:
            try:
                convert_cover_letter_to_pdf(f, page=page)
                converted += 1
            except Exception as e:
                log.error("Failed to convert cover letter %s: %s", f.name, e)
        browser.close()

    log.info("Done: %d/%d cover letter PDFs generated in %s", converted, len(to_convert), COVER_LETTER_DIR)
    return converted


def batch_convert(limit: int = 50) -> int:
    """Convert .txt files in TAILORED_DIR and COVER_LETTER_DIR that don't have corresponding PDFs.

    Scans for .txt files in TAILORED_DIR (excluding _JOB.txt) and COVER_LETTER_DIR,
    checks if a .pdf with the same stem already exists, and converts any that are missing.

    Args:
        limit: Maximum number of files to convert per directory.

    Returns:
        Number of PDFs generated.
    """
    cl_converted = batch_convert_cover_letters(limit=limit)

    if not TAILORED_DIR.exists():
        log.warning("Tailored directory does not exist: %s", TAILORED_DIR)
        return cl_converted

    txt_files = sorted(TAILORED_DIR.glob("*.txt"))
    candidates = [
        f for f in txt_files
        if not f.name.endswith("_JOB.txt") and not f.name.endswith("_CL.txt")
    ]

    to_convert: list[Path] = []
    for f in candidates:
        pdf_path = f.with_suffix(".pdf")
        if not pdf_path.exists():
            to_convert.append(f)
        if len(to_convert) >= limit:
            break

    if not to_convert:
        log.info("All resume text files already have PDFs.")
        return cl_converted

    from playwright.sync_api import sync_playwright

    log.info("Converting %d resume files to PDF...", len(to_convert))
    converted = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for f in to_convert:
            try:
                convert_to_pdf(f, page=page)
                converted += 1
            except Exception as e:
                log.error("Failed to convert %s: %s", f.name, e)
        browser.close()

    log.info("Done: %d/%d PDFs generated in %s", converted, len(to_convert), TAILORED_DIR)
    return converted + cl_converted
