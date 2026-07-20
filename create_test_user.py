"""
Creates one real user row in the database, matching the same data
your onboarding mockup collects. This replaces the hardcoded test
profile with an actual account build_paper.py can read from.

Run once to create yourself as a real test user:
  python3 create_test_user.py
"""

import os
import json
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.environ["DATABASE_URL"])

# This is what the onboarding screens would submit — same shape,
# just typed in directly here since we don't have the frontend wired
# up yet.
email = "priyanshi@example.com"
sources = ["BBC", "Al Jazeera", "The Guardian", "NPR"]
topic_weights = {
    "Middle East": 2,
    "Technology": 2,
    "World": 1,
    "Headlines": 1,
}
reading_time_minutes = 30

with conn.cursor() as cur:
    cur.execute(
        """
        INSERT INTO users (email, sources, topic_weights, reading_time_minutes)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET
            sources = EXCLUDED.sources,
            topic_weights = EXCLUDED.topic_weights,
            reading_time_minutes = EXCLUDED.reading_time_minutes
        RETURNING id;
        """,
        (email, sources, json.dumps(topic_weights), reading_time_minutes),
    )
    user_id = cur.fetchone()[0]

conn.commit()
conn.close()

print(f"User created/updated — id: {user_id}, email: {email}")
