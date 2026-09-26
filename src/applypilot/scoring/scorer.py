"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from applypilot.config import RESUME_PATH, load_profile
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.discovery.company_filter import is_job_allowed, get_company_tier
from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT = """You are a job fit evaluator for a candidate targeting top product-based companies and high-growth product startups. Given a candidate's resume and a job description, score how well the candidate fits the role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills at a strong product organization or top startup.
- 7-8: Strong match. Candidate has most required technical skills and domain strengths, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has some relevant skills, but missing key core technologies or experience.
- 3-4: Weak match. Significant skill gaps, or role appears to be a staffing agency, IT consultancy, or client services vendor.
- 1-2: Poor match. Completely different field, pure IT services/body shop, OR QA/Testing/SDET/Automation Testing role.

IMPORTANT FACTORS:
- TARGET ROLES: Candidate ONLY targets Software Development (Backend, Full Stack, Systems, Cloud, AI/ML) and NOT Quality Assurance / Testing / SDET.
- STRICT REJECTION FOR QA & TESTING: If the role is QA, Software Testing, SDET, Manual Tester, or Automation Test Engineer, assign a score of 1-2. Candidate is a Software Developer, NOT a Tester.
- TARGET COMPANIES: Candidate targets top product-based tech companies (Tier 1/Tier 2 like Google, Uber, Stripe, Atlassian) and top product startups (Series B+, unicorns in SaaS, Cloud, DevTools, Fintech, AI/ML).
- HEAVY PENALTY FOR SERVICES / CONSULTING / STAFFING: If the company or job description indicates an IT service firm, consultancy, staffing vendor, C2C, or third-party client project, heavily penalize the score (max 1-3).
- PRODUCT FOCUS BONUS: Reward jobs building proprietary core products, scalable cloud platforms, modern distributed systems, and real tech engineering.
- Weight technical skills heavily (programming languages, frameworks, distributed systems, databases, APIs)
- Factor in the candidate's real engineering and project depth
- Be realistic about experience level vs. job requirements (years of experience, seniority)

RESPOND IN EXACTLY THIS FORMAT (no other text):
SCORE: [1-10]
KEYWORDS: [comma-separated ATS keywords from the job description that match or could match the candidate]
REASONING: [2-3 sentences explaining the score, explicitly noting product company fit]"""


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    score = 0
    keywords = ""
    reasoning = response

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score = int(re.search(r"\d+", line).group())
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()

    return {"score": score, "keywords": keywords, "reasoning": reasoning}


def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, company, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    company = job.get("company") or job.get("site") or "Unknown"
    title = job.get("title") or ""
    full_desc = job.get("full_description") or job.get("description") or ""

    # Fast-reject blocked companies, staffing agencies, QA/testing, or excluded titles without wasting LLM tokens
    allowed, block_reason = is_job_allowed(title, company, full_desc)
    if not allowed:
        log.info("Fast-rejecting '%s' at '%s': %s", title, company, block_reason)
        return {
            "score": 1,
            "keywords": "",
            "reasoning": f"Filtered: {block_reason}",
        }

    company_tier = get_company_tier(company)

    job_text = (
        f"TITLE: {title}\n"
        f"COMPANY: {company} (Tier: {company_tier})\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{full_desc[:6000]}"
    )

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        client = get_client()
        response = client.chat(messages, max_tokens=512, temperature=0.2)
        return _parse_score_response(response)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return {"score": 0, "keywords": "", "reasoning": f"LLM error: {e}"}


def run_scoring(limit: int = 0, rescore: bool = False, target_qualified: int = 0, min_score: int = 7) -> dict:
    """Score unscored jobs that have full descriptions.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).
        target_qualified: If > 0, stop once this many jobs score >= min_score.
        min_score: Minimum score to count toward target_qualified.

    Returns:
        {"scored": int, "errors": int, "elapsed": float, "distribution": list, "qualified": int}
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": [], "qualified": 0}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    log.info("Scoring %d jobs sequentially...", len(jobs))
    t0 = time.time()
    completed = 0
    errors = 0
    qualified_count = 0
    results: list[dict] = []

    for job in jobs:
        result = score_job(resume_text, job)
        result["url"] = job["url"]
        completed += 1

        if result["score"] == 0:
            errors += 1
        elif result["score"] >= min_score:
            qualified_count += 1

        results.append(result)

        log.info(
            "[%d/%d] score=%d  %s",
            completed, len(jobs), result["score"], job.get("title", "?")[:60],
        )

        if target_qualified > 0 and qualified_count >= target_qualified:
            log.info("Reached batch target of %d qualified jobs (score >= %d). Stopping scoring loop.", target_qualified, min_score)
            break

    # Write scores to DB
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        reasoning_text = f"{r['keywords']}\n{r['reasoning']}".strip()
        if r["score"] == 0 and "LLM error" in r.get("reasoning", ""):
            # Leave fit_score as NULL for transient LLM errors so subsequent runs automatically retry
            conn.execute(
                "UPDATE jobs SET score_reasoning = ? WHERE url = ?",
                (reasoning_text, r["url"]),
            )
        else:
            conn.execute(
                "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ? WHERE url = ?",
                (r["score"], reasoning_text, now, r["url"]),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info("Done: %d scored in %.1fs (%.1f jobs/sec)", len(results), elapsed, len(results) / elapsed if elapsed > 0 else 0)

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": len(results),
        "errors": errors,
        "elapsed": elapsed,
        "distribution": distribution,
    }
