"""
Runs story synthesis on every story missing entity tags, and saves the
results (headline, summary, key facts, disputed facts, importance,
topics, entities) into the stories table.
"""

import os
import json
import time
import psycopg2
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.environ["GROQ_API_KEY"])
conn = psycopg2.connect(os.environ["DATABASE_URL"])

ALLOWED_TOPICS = [
    "Headlines", "World", "U.S. News", "Politics", "Middle East", "Europe",
    "Asia", "Africa", "Americas", "Technology", "Business & Economy",
    "Markets & Finance", "Science", "Health", "Climate & Environment",
    "Culture", "Arts & Books", "Film & TV", "Sports", "Opinion & Analysis",
    "Education", "Travel", "Food & Drink",
]


def build_prompt(articles):
    articles_text = "\n\n---\n\n".join(
        f"SOURCE: {source}\nTITLE: {title}\nBODY: {body or ''}"
        for title, source, body in articles
    )
    topics_list = ", ".join(ALLOWED_TOPICS)
    return f"""You are given one or more news articles covering the same event.

{articles_text}

Respond with ONLY a JSON object (no markdown, no backticks, no preamble) with this exact shape:

{{
  "headline": "a clear, neutral headline for this story",
  "summary": "a 2-3 sentence summary of what happened",
  "key_facts": ["fact 1", "fact 2", "fact 3"],
  "disputed_facts": ["anything sources disagree on, or an empty list if none"],
  "importance_score": <integer 1-10, where 10 = major global historical event, 5 = notable national/regional news, 1 = minor or niche-interest story>,
  "topics": [<1-3 topics from this exact list that best fit this story: {topics_list}>],
  "entities": [<1-3 SHORT specific names for the actual ongoing situation this story is part of, e.g. "US-Iran conflict", "Israel-Gaza war", "UK Labour government", "FIFA World Cup 2026" — these should be specific enough to distinguish this from OTHER stories that share the same broad topic>]
}}"""


def main():
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM stories WHERE entities IS NULL ORDER BY id;")
        story_ids = [row[0] for row in cur.fetchall()]

    print(f"Synthesizing {len(story_ids)} stories...\n")

    for story_id in story_ids:
        with conn.cursor() as cur:
            cur.execute("SELECT title, source, body FROM articles WHERE story_id = %s;", (story_id,))
            articles = cur.fetchall()

        prompt = build_prompt(articles)

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE stories SET
                        headline = %s,
                        summary = %s,
                        key_facts = %s,
                        disputed_facts = %s,
                        importance_score = %s,
                        topics = %s,
                        entities = %s,
                        updated_at = now()
                    WHERE id = %s;
                    """,
                    (
                        result["headline"],
                        result["summary"],
                        result.get("key_facts", []),
                        result.get("disputed_facts", []),
                        result.get("importance_score", 5),
                        result.get("topics", []),
                        result.get("entities", []),
                        story_id,
                    ),
                )
            conn.commit()
            print(f"  #{story_id} — {result['headline']}  |  entities: {result.get('entities')}")

        except Exception as e:
            print(f"  #{story_id} FAILED: {e}")

        time.sleep(1.2)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
