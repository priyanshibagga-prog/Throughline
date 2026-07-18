"""
Pretty-prints one story's full content, including the historical context
and recent timeline — avoids the terminal-width truncation you get from
psql when the content is long.

Usage: python3 view_story.py <story_id>
"""

import os
import sys
import json
import psycopg2
from dotenv import load_dotenv

load_dotenv()

if len(sys.argv) < 2:
    print("Usage: python3 view_story.py <story_id>")
    sys.exit(1)

story_id = sys.argv[1]

conn = psycopg2.connect(os.environ["DATABASE_URL"])
with conn.cursor() as cur:
    cur.execute("""
        SELECT headline, summary, topics, entities, importance_score,
               historical_context, recent_timeline
        FROM stories WHERE id = %s;
    """, (story_id,))
    row = cur.fetchone()

if not row:
    print(f"No story with id {story_id}")
    sys.exit(1)

headline, summary, topics, entities, importance, historical, recent = row

print(f"HEADLINE: {headline}")
print(f"SUMMARY: {summary}")
print(f"TOPICS: {topics}")
print(f"ENTITIES: {entities}")
print(f"IMPORTANCE: {importance}\n")

print("--- HISTORICAL CONTEXT ---")
if historical:
    for entry in historical:
        print(f"  {entry['date']}: {entry['text']}")
else:
    print("  (none — not applicable to this story, or not generated yet)")

print("\n--- RECENT TIMELINE ---")
if recent:
    for entry in recent:
        print(f"  {entry['date']}: {entry['text']}")
else:
    print("  (none — not enough spread across days, or not generated yet)")

conn.close()
