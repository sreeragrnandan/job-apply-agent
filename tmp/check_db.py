import sqlite3
import json
from pathlib import Path

db_path = Path.home() / ".applypilot" / "applypilot.db"
if not db_path.exists():
    print("Database not found at", db_path)
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
print("=== DB SUMMARY ===")
print(f"Total jobs in DB: {total}")

scored_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]
unscored_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NULL").fetchone()[0]
has_desc = conn.execute("SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL AND full_description != ''").fetchone()[0]
no_desc = conn.execute("SELECT COUNT(*) FROM jobs WHERE full_description IS NULL OR full_description = ''").fetchone()[0]

print(f"Jobs with fit_score: {scored_count}")
print(f"Jobs with fit_score IS NULL: {unscored_count}")
print(f"Jobs with full_description: {has_desc}")
print(f"Jobs without full_description (short desc only): {no_desc}")

print("\n=== SCORE DISTRIBUTION ===")
dist = conn.execute("SELECT fit_score, COUNT(*) as cnt FROM jobs GROUP BY fit_score ORDER BY fit_score ASC").fetchall()
for r in dist:
    print(f"  Score {r['fit_score']}: {r['cnt']} jobs")

print("\n=== SAMPLE REASONING BEHIND SCORE 0 JOBS ===")
score_0_sample = conn.execute("SELECT url, title, company, fit_score, score_reasoning FROM jobs WHERE fit_score = 0 LIMIT 10").fetchall()
for row in score_0_sample:
    print(f"Title: {row['title']} | Company: {row['company']} | Score: {row['fit_score']}")
    print(f"Reasoning:\n{row['score_reasoning']}")
    print("-" * 60)

print("\n=== SAMPLE REASONING BEHIND SCORE 1 JOBS ===")
score_1_sample = conn.execute("SELECT url, title, company, fit_score, score_reasoning FROM jobs WHERE fit_score = 1 LIMIT 5").fetchall()
for row in score_1_sample:
    print(f"Title: {row['title']} | Company: {row['company']} | Score: {row['fit_score']}")
    print(f"Reasoning:\n{row['score_reasoning']}")
    print("-" * 60)

print("\n=== SAMPLE JOBS TIERS AND TITLES IN DB ===")
titles_sample = conn.execute("SELECT title, company, location, site, fit_score FROM jobs LIMIT 20").fetchall()
for row in titles_sample:
    print(f"Score: {row['fit_score']} | Site: {row['site']} | Title: {row['title']} | Location: {row['location']}")
