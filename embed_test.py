"""
Pulls one real article from the database and turns it into an embedding —
using a model that runs locally on your own laptop, completely free.

First time you run this, it'll download the model (~80MB), which takes
a minute. After that, it's instant and works even offline.
"""

import os
import psycopg2
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

print("Loading model (first run downloads it, ~80MB)...")
model = SentenceTransformer("all-MiniLM-L6-v2")

conn = psycopg2.connect(os.environ["DATABASE_URL"])
with conn.cursor() as cur:
    cur.execute("SELECT id, title, body FROM articles ORDER BY published_at DESC LIMIT 1;")
    article_id, title, body = cur.fetchone()

print(f"\nArticle: {title}\n")

# We embed the title + body together, since the meaning of the article
# lives in both, not just the headline.
text_to_embed = f"{title}\n\n{body or ''}"

embedding = model.encode(text_to_embed)

print(f"Embedding length: {len(embedding)} numbers")
print(f"First 10 numbers: {list(embedding[:10])}")

conn.close()
