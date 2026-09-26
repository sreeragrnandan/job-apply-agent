"""Company and role filtering: Enforce product-company focus and block agency/IT services.

Filters out:
- IT services & outsourcing companies (TCS, Infosys, Wipro, Accenture, Cognizant, etc.)
- Staffing, recruiting agencies, and third-party body shops
- BPO/KPO and non-tech call centers
- Consulting firms (Big 4, management consulting)
- Government / PSU postings
- Contract/C2C/third-party payroll postings
- Unwanted title patterns (internships, executive director/VP roles, non-tech roles)

Prioritizes:
- Tier 1 & Tier 2 product companies (FAANG, Big Tech, high-growth scale-ups)
- Top tech startups and unicorns (Series B+, VC-backed product builders)
- Preferred product domains: SaaS/Cloud, Fintech, AI/ML, Healthtech, E-commerce, Edtech, DevTools
"""

from __future__ import annotations

import logging
import re
from typing import Any

from applypilot import config

log = logging.getLogger(__name__)

# ── Built-in Hard Blocklists ────────────────────────────────────────────────

DEFAULT_BLOCKED_COMPANIES = [
    # Top IT Services & Outsourcing (India & Global)
    "tcs",
    "tata consultancy",
    "infosys",
    "wipro",
    "cognizant",
    "accenture",
    "hcl",
    "hcltech",
    "hcl technologies",
    "capgemini",
    "tech mahindra",
    "ltimindtree",
    "mindtree",
    "l&t infotech",
    "l&t technology services",
    "ltts",
    "mphasis",
    "hexaware",
    "birlasoft",
    "zensar",
    "coforge",
    "niit technologies",
    "cyient",
    "kpit",
    "sonata software",
    "ust global",
    "virtusa",
    "epam",
    "dxc technology",
    "dxc",
    "atos",
    "cgi group",
    "cgi inc",
    "ntt data",
    "syntel",
    "igate",
    "mastek",
    "firstsource",
    "happiest minds",
    "tata elxsi",
    "sopra steria",

    # Contract Staffing & Body Shops (reputable permanent search firms like Randstad,
    # Michael Page, Adecco are allowed unless flagged for C2C / third-party payroll)
    "teamlease",
    "quess corp",
    "allegis",
    "teksystems",
    "aerotek",
    "aston carter",
    "robert half",
    "kforce",
    "manpower",
    "manpowergroup",
    "experis",
    "hays",
    "collabera",
    "apex systems",
    "insight global",
    "nesco resource",
    "modis",
    "synergisticit",
    "diverselynx",
    "diverse lynx",
    "compunnel",
    "idexcel",
    "v-soft",
    "infotree",
    "disys",
    "digital intelligence systems",
    "pyramid consulting",
    "judge group",
    "beacon hill",
    "addison group",
    "staffing solutions",
    "recruitment solutions",
    "talent consulting",
    "staffing agency",
    "hr solutions",

    # BPO, KPO & Customer Operations
    "teleperformance",
    "concentrix",
    "genpact",
    "wns global",
    "wns",
    "exl service",
    "exl",
    "sutherland",
    "sutherland global",
    "hinduja global",
    "hgs",
    "startek",
    "ienergizer",
    "conduent",
    "alorica",
    "taskus",

    # Big 4 & Management Consulting
    "deloitte",
    "pwc",
    "pricewaterhousecoopers",
    "ey",
    "ernst & young",
    "kpmg",
    "mckinsey",
    "boston consulting group",
    "bcg",
    "bain & company",
    "oliver wyman",
    "booz allen",

    # Government / PSU / Defense
    "national informatics centre",
    "nic",
    "cdac",
    "drdo",
    "isro",
    "bel",
    "bhel",
    "iocl",
    "ongc",
    "ntpc",
    "sail",
    "state bank of india",
    "sbi",
    "psu",
    "ministry of",
    "municipal corporation",
]

# Phrases in job description indicating body shopping, subcontracting, or agency placement
DEFAULT_BLOCKED_DESCRIPTION_KEYWORDS = [
    "c2c",
    "corp-to-corp",
    "corp 2 corp",
    "corp-to-corp / 1099",
    "third-party payroll",
    "third party payroll",
    "on client payroll",
    "on the payroll of",
    "our direct client",
    "our prime vendor",
    "our tier 1 vendor",
    "our client is a leading",
    "our client is looking",
    "our client is seeking",
    "client location",
    "work at client site",
    "client project",
    "deputed to client",
    "deputed at",
    "deputation at",
    "staffing firm",
    "staffing agency",
    "recruitment firm",
    "recruitment agency",
    "placement agency",
    "bench sales",
    "contract to hire",
    "contract-to-hire",
    "body shopping",
    "subcontracting",
    "no c2c",
]

# Negative title keywords -- skip jobs with these in the title
DEFAULT_EXCLUDE_TITLES = [
    "intern",
    "internship",
    "trainee",
    "graduate trainee",
    "campus",
    "fellowship",
    "unpaid",
    "senior director",
    "director",
    "managing director",
    "vp ",
    "vice president",
    "svp",
    "avp",
    "chief",
    "head of",
    "principal scientist",
    "clearance required",
    "ts/sci",
    "polygraph",
    "recruiter",
    "talent acquisition",
    "sales executive",
    "bpo executive",
    "telecaller",
    "customer support executive",
    "accountant",
    "marketing manager",
    "sdet",
    "qa",
    "quality assurance",
    "tester",
    "testing",
    "test engineer",
    "automation test engineer",
    "qa engineer",
    "qa analyst",
    "qa lead",
    "test lead",
    "manual tester",
    "software test engineer",
    "software engineer in test",
]

# Tier 1 & Tier 2 Product Companies (Known high-bar engineering teams)
TIER1_COMPANIES = {
    "google", "alphabet", "meta", "facebook", "apple", "microsoft", "amazon",
    "netflix", "uber", "airbnb", "stripe", "atlassian", "datadog", "snowflake",
    "palantir", "coinbase", "figma", "notion", "linear", "github", "gitlab",
    "doordash", "instacart", "pinterest", "reddit", "slack", "zoom", "twilio",
    "dropbox", "shopify", "openai", "anthropic", "scale ai", "databricks",
    "nvidia", "salesforce", "adobe", "cisco", "intel", "paypal", "mastercard",
    "visa", "spotify", "lyft", "box", "canva", "miro", "brex", "ramp", "plaid",
    "rippling", "deel", "elastic", "mongodb", "hashicorp", "cloudflare", "twelve labs"
}

# Top tech unicorns & Series B+ startups (India & Global)
TOP_STARTUPS = {
    "razorpay", "swiggy", "zomato", "cred", "phonepe", "flipkart", "meesho",
    "urban company", "postman", "browserstack", "hasura", "inmobi", "freshworks",
    "zoho", "zepto", "zerodha", "groww", "pine labs", "slice", "jupiter",
    "porter", "dream11", "khatabook", "clevertap", "chargebee",
    "harness", "browserstack", "loconav", "unacademy", "upstox", "mpl",
    "coinswitch", "navi", "jupiter money", "incred", "cars24", "spinny",
    "coinbase", "revolut", "koinx","cloudstrike","makemytrip"
}

# Preferred product domains
PREFERRED_DOMAINS = [
    "saas",
    "cloud",
    "developer tools",
    "devtools",
    "fintech",
    "payments",
    "banking technology",
    "ai",
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "llm",
    "mlops",
    "healthtech",
    "digital health",
    "e-commerce",
    "ecommerce",
    "marketplace",
    "edtech",
    "cybersecurity",
    "infosec",
]


# ── Filter Configuration Loader ─────────────────────────────────────────────

def get_filter_config(cfg: dict | None = None) -> dict[str, Any]:
    """Retrieve combined company and role filter configuration."""
    if cfg is None:
        try:
            cfg = config.load_search_config()
        except Exception as e:
            log.warning("Could not load search config for filtering: %s", e)
            cfg = {}

    company_filter = cfg.get("company_filter", {}) if isinstance(cfg, dict) else {}

    # Merge custom blocked companies with built-ins
    custom_blocked_companies = company_filter.get("blocked_companies", [])
    merged_blocked_companies = list(set(
        [c.strip().lower() for c in DEFAULT_BLOCKED_COMPANIES] +
        [c.strip().lower() for c in custom_blocked_companies if isinstance(c, str)]
    ))

    # Merge custom excluded titles with built-ins
    custom_exclude_titles = cfg.get("exclude_titles", []) if isinstance(cfg, dict) else []
    merged_exclude_titles = list(set(
        [t.strip().lower() for t in DEFAULT_EXCLUDE_TITLES] +
        [t.strip().lower() for t in custom_exclude_titles if isinstance(t, str)]
    ))

    # Merge description red flags
    custom_blocked_desc = company_filter.get("blocked_description_keywords", [])
    merged_blocked_desc = list(set(
        [k.strip().lower() for k in DEFAULT_BLOCKED_DESCRIPTION_KEYWORDS] +
        [k.strip().lower() for k in custom_blocked_desc if isinstance(k, str)]
    ))

    # Custom whitelist / preferred companies
    custom_allowed_companies = [
        c.strip().lower() for c in company_filter.get("allowed_companies", []) if isinstance(c, str)
    ]

    return {
        "enabled": company_filter.get("enabled", True),
        "only_product_companies": company_filter.get("only_product_companies", True),
        "min_company_size": company_filter.get("min_company_size", 50),
        "blocked_companies": merged_blocked_companies,
        "blocked_description_keywords": merged_blocked_desc,
        "exclude_titles": merged_exclude_titles,
        "allowed_companies": custom_allowed_companies,
        "preferred_domains": PREFERRED_DOMAINS,
    }


# ── Filter Check Functions ──────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Normalize company name: lowercase, strip punctuation and extra spaces."""
    if not name:
        return ""
    name = re.sub(r"[^\w\s]", " ", name.lower())
    return " ".join(name.split())


def is_company_blocked(company_name: str | None, filter_cfg: dict | None = None) -> tuple[bool, str]:
    """Check if a company matches the blocked IT services / staffing list.

    Returns:
        (is_blocked, reason)
    """
    if not company_name or str(company_name).strip().lower() in ("nan", "none", "unknown"):
        return False, ""

    cfg = filter_cfg or get_filter_config()
    if not cfg.get("enabled", True):
        return False, ""

    comp_clean = _normalize_name(company_name)
    if not comp_clean:
        return False, ""

    # Check explicit whitelist bypass first
    allowed_list = cfg.get("allowed_companies", [])
    for allowed in allowed_list:
        if allowed and (allowed == comp_clean or f" {allowed} " in f" {comp_clean} "):
            return False, ""

    # Check blocked list
    blocked_list = cfg.get("blocked_companies", [])
    for blocked in blocked_list:
        if not blocked:
            continue
        blocked_norm = _normalize_name(blocked)
        if not blocked_norm:
            continue

        # Exact match or word boundary match
        pattern = r"(^|\b)" + re.escape(blocked_norm) + r"(\b|$)"
        if re.search(pattern, comp_clean):
            return True, f"Blocked company: matched '{blocked}'"

    return False, ""


def is_title_blocked(title: str | None, filter_cfg: dict | None = None) -> tuple[bool, str]:
    """Check if a job title matches excluded title patterns (intern, VP, director, etc.).

    Returns:
        (is_blocked, reason)
    """
    if not title:
        return False, ""

    cfg = filter_cfg or get_filter_config()
    title_lower = title.strip().lower()

    for pattern in cfg.get("exclude_titles", []):
        if not pattern:
            continue
        pat_clean = pattern.strip().lower()
        if pat_clean in title_lower:
            return True, f"Excluded title: matched '{pat_clean}'"

    return False, ""


def is_description_blocked(description: str | None, filter_cfg: dict | None = None) -> tuple[bool, str]:
    """Check if a job description contains agency / subcontracting / C2C red flags.

    Returns:
        (is_blocked, reason)
    """
    if not description:
        return False, ""

    cfg = filter_cfg or get_filter_config()
    desc_lower = description.lower()

    for red_flag in cfg.get("blocked_description_keywords", []):
        if not red_flag:
            continue
        flag_clean = red_flag.strip().lower()
        pattern = r"(^|\b)" + re.escape(flag_clean) + r"(\b|$)"
        if re.search(pattern, desc_lower):
            return True, f"Agency/outsourcing flag in description: '{flag_clean}'"

    return False, ""


DEFAULT_BLOCKED_LOCATIONS = [
    "mexico", "mexico city", "usa", "united states", "us", "uk", "united kingdom",
    "canada", "australia", "germany", "poland", "philippines", "manila",
    "spain", "france", "brazil", "colombia", "costa rica", "romania",
    "singapore", "japan", "latam", "latin america", "sweden", "switzerland",
    "netherlands", "ireland", "korea", "seoul", "eagan-minnesota", "new-york", "frisco-texas",
    "zug-zug", "gothenburg", "langenfeld", "toronto-ontario"
]

def is_location_blocked(
    location: str | None,
    filter_cfg: dict | None = None,
    url: str | None = None,
    description: str | None = None,
) -> tuple[bool, str]:
    """Check if job location is outside target region (India)."""
    text_to_check = f"{location or ''} {url or ''} {description[:500] if description else ''}".lower()
    if not text_to_check.strip():
        return False, ""
    
    # If explicitly mentions India or Indian tech hubs, allow
    india_hubs = ["india", "bengaluru", "bangalore", "hyderabad", "pune", "delhi", "noida", "gurgaon", "mumbai", "chennai"]
    if any(hub in text_to_check for hub in india_hubs):
        return False, ""
        
    for blocked_loc in DEFAULT_BLOCKED_LOCATIONS:
        if blocked_loc in text_to_check:
            return True, f"Foreign job location: matched '{blocked_loc}'"
            
    return False, ""

def is_job_allowed(
    title: str | None,
    company: str | None,
    description: str | None = None,
    filter_cfg: dict | None = None,
    location: str | None = None,
    url: str | None = None,
) -> tuple[bool, str]:
    """Master filter check for a job.

    Returns:
        (allowed, rejection_reason)
    """
    cfg = filter_cfg or get_filter_config()

    # 1. Title exclusion check
    t_blocked, t_reason = is_title_blocked(title, cfg)
    if t_blocked:
        return False, t_reason

    # 2. Company blocklist check
    c_blocked, c_reason = is_company_blocked(company, cfg)
    if c_blocked:
        return False, c_reason

    # 3. Location check
    l_blocked, l_reason = is_location_blocked(location, cfg, url=url, description=description)
    if l_blocked:
        return False, l_reason

    # 4. Description red flag check
    if description:
        d_blocked, d_reason = is_description_blocked(description, cfg)
        if d_blocked:
            return False, d_reason

    return True, ""


def get_company_tier(company_name: str | None) -> str:
    """Classify company tier for scoring and dashboard display.

    Returns:
        "tier_1" | "tier_2" | "top_startup" | "product"
    """
    if not company_name:
        return "product"

    norm = _normalize_name(company_name)

    for c in TIER1_COMPANIES:
        if c in norm or norm in c:
            return "tier_1"

    for c in TOP_STARTUPS:
        if c in norm or norm in c:
            return "top_startup"

    return "product"
