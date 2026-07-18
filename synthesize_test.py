"""
Picks one story that has multiple articles, sends all of them to a free
LLM (via Groq) together, and asks for a structured summary. We're just
looking at the result first, not saving anything yet.
"""

import os
import json
import psycopg2
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.environ["GROQ_API_KEY"])

conn = psycopg2.connect(os.environ["DATABASE_URL"])

with conn.cursor() as cur:
    # Find a story with the most articles grouped into it — the richest
    # test case, since a single-article "story" isn't interesting yet.
    cur.execute("""
        SELECT story_id, COUNT(*) as n
        FROM articles
        WHERE story_id IS NOT NULL
        GROUP BY story_id
        ORDER BY n DESC
        LIMIT 1;
    """)
    story_id, article_count = cur.fetchone()

    cur.execute("SELECT title, source, body FROM articles WHERE story_id = %s;", (story_id,))
    articles = cur.fetchall()

print(f"Testing on story #{story_id} — {article_count} articles:\n")
for title, source, _ in articles:
    print(f"  [{source}] {title}")

articles_text = "\n\n---\n\n".join(
    f"SOURCE: {source}\nTITLE: {title}\nBODY: {body or ''}"
    for title, source, body in articles
)

prompt = f"""You are given several news articles from different outlets, all covering the same event.

{articles_text}

Based only on these articles, respond with ONLY a JSON object (no markdown, no backticks, no preamble) with this exact shape:

{{
  "headline": "a clear, neutral headline for this story",
  "summary": "a 2-3 sentence summary combining what all sources agree on",
  "key_facts": ["fact 1", "fact 2", "fact 3"],
  "disputed_facts": ["anything sources disagree on or report differently, or an empty list if none"]
}}"""

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": prompt}],
    response_format={"type": "json_object"},
)

result = json.loads(response.choices[0].message.content)

print("\n--- RESULT ---\n")
print(json.dumps(result, indent=2))

conn.close()
