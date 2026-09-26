import sqlite3
from pathlib import Path

db = Path.home() / ".applypilot" / "applypilot.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

cur.execute("UPDATE jobs SET fit_score = NULL, scored_at = NULL WHERE fit_score = 0 AND score_reasoning LIKE '%LLM error%'")
reset_count = cur.rowcount
conn.commit()

print(f"Successfully reset {reset_count} jobs with LLM network errors back to fit_score = NULL.")

total_null = cur.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NULL").fetchone()[0]
total_scored = cur.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]
print(f"Current DB status: {total_null} jobs ready for AI scoring, {total_scored} jobs already scored.")
