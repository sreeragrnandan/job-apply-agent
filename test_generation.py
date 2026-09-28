import sys
import io
from pathlib import Path

# Force UTF-8 on Windows stdout/stderr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from applypilot.config import load_env, load_profile, RESUME_PATH
from applypilot.database import get_connection
from applypilot.scoring.tailor import tailor_resume
from applypilot.scoring.cover_letter import generate_cover_letter
from applypilot.scoring.pdf import convert_to_pdf, convert_cover_letter_to_pdf

load_env()
profile = load_profile()
resume_text = RESUME_PATH.read_text(encoding="utf-8")

conn = get_connection()
row = conn.execute(
    "SELECT * FROM jobs WHERE fit_score >= 8 AND full_description IS NOT NULL LIMIT 1"
).fetchone()

if not row:
    print("No job with fit_score >= 8 found, trying fit_score >= 7...")
    row = conn.execute(
        "SELECT * FROM jobs WHERE fit_score >= 7 AND full_description IS NOT NULL LIMIT 1"
    ).fetchone()

if not row:
    print("Error: No suitable job found in database.")
    sys.exit(1)

job = dict(row)
print(f"Target Job: {job['title']} at {job['site']}")
print(f"Job URL: {job['url']}")
print("-" * 50)

# Check if resume was already tailored to avoid re-calling if available
test_res_txt = Path("test_resume.txt")
if not test_res_txt.exists():
    print("\n[1/4] Generating tailored resume with LLM...")
    tailored_text, report = tailor_resume(resume_text, job, profile, validation_mode="normal")
    print(f"Status: {report.get('status')}, Attempts: {report.get('attempts')}")
    test_res_txt.write_text(tailored_text, encoding="utf-8")
    print(f"Saved: {test_res_txt.resolve()}")
else:
    print(f"\n[1/4] Using existing test_resume.txt ({test_res_txt.resolve()})")

# 2. Render Resume to PDF
print("\n[2/4] Converting tailored resume to PDF...")
try:
    res_pdf = convert_to_pdf(test_res_txt)
    print(f"[OK] Resume PDF created: {res_pdf.resolve()}")
except Exception as e:
    print(f"[ERROR] Failed to render Resume PDF: {e}")

# 3. Generate Cover Letter
print("\n[3/4] Generating cover letter with LLM...")
cl_text = generate_cover_letter(resume_text, job, profile, validation_mode="normal")

test_cl_txt = Path("test_cover_letter_CL.txt")
test_cl_txt.write_text(cl_text, encoding="utf-8")
print(f"Saved: {test_cl_txt.resolve()}")

# 4. Render Cover Letter to PDF
print("\n[4/4] Converting cover letter to PDF...")
try:
    cl_pdf = convert_cover_letter_to_pdf(test_cl_txt, profile=profile)
    print(f"[OK] Cover Letter PDF created: {cl_pdf.resolve()}")
except Exception as e:
    print(f"[ERROR] Failed to render Cover Letter PDF: {e}")

print("\n" + "=" * 50)
print("Generation finished successfully!")
