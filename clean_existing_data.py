"""
One-time cleanup: strips HTML tags and decodes entities in text that was
already ingested before the fix — both raw articles and anything the LLM
generated from them (which inherited the same dirty text).
Safe to run multiple times.
"""

import os
import psycopg2
from dotenv import load_dotenv
from text_utils import clean_text

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])

with conn.cursor() as cur:
    cur.execute("SELECT id, title, body FROM articles;")
    articles = cur.fetchall()

print(f"Cleaning {len(articles)} articles...")
for article_id, title, body in articles:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE articles SET title = %s, body = %s WHERE id = %s;",
            (clean_text(title), clean_text(body), article_id),
        )
conn.commit()

with conn.cursor() as cur:
    cur.execute("SELECT id, headline, summary FROM stories;")
    stories = cur.fetchall()

print(f"Cleaning {len(stories)} stories...")
for story_id, headline, summary in stories:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE stories SET headline = %s, summary = %s WHERE id = %s;",
            (clean_text(headline), clean_text(summary), story_id),
        )
conn.commit()

conn.close()
print("Done.")
