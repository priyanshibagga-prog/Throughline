"""
Embeds every article in the database that doesn't have an embedding yet,
and saves the numbers back into the articles table.

Run this after ingest.py brings in new articles — think of it as the
second step in the pipeline: fetch articles, then embed the new ones.
"""

import os
import psycopg2
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

print("Loading model...")
model = SentenceTransformer("all-MiniLM-L6-v2")

conn = psycopg2.connect(os.environ["DATABASE_URL"])
register_vector(conn)  # teaches psycopg2 how to send embeddings to the vector column

with conn.cursor() as cur:
    # Only grab articles that haven't been embedded yet — this is what makes
    # it safe to re-run this script after every ingest without redoing work.
    cur.execute("SELECT id, title, body FROM articles WHERE embedding IS NULL;")
    rows = cur.fetchall()

print(f"Found {len(rows)} articles without an embedding.")

for article_id, title, body in rows:
    text_to_embed = f"{title}\n\n{body or ''}"
    embedding = model.encode(text_to_embed)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE articles SET embedding = %s WHERE id = %s;",
            (embedding, article_id),
        )

conn.commit()
conn.close()

print(f"Done — embedded {len(rows)} articles.")
