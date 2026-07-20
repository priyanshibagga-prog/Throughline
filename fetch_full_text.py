"""
Fetches the REAL, complete article from its actual webpage — RSS feeds
only give a short teaser, not the full text, so this replaces that short
snippet with the genuine article content wherever we can get it.

Resumable: only processes articles that haven't been attempted yet
(full_text_fetched = FALSE), so re-running this only does new work.
"""

import os
import time
import psycopg2
import trafilatura
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.environ["DATABASE_URL"])

with conn.cursor() as cur:
    cur.execute("SELECT id, url, body FROM articles WHERE full_text_fetched IS NOT TRUE;")
    rows = cur.fetchall()

print(f"Fetching full text for {len(rows)} articles...\n")

for article_id, url, old_body in rows:
    try:
        downloaded = trafilatura.fetch_url(url)
        full_text = trafilatura.extract(downloaded) if downloaded else None

        # Only replace what we had if the scrape actually got something
        # longer/better than the RSS snippet — some sites block scraping
        # or paywall content, in which case we just keep the short version.
        if full_text and len(full_text) > len(old_body or ""):
            new_body = full_text
            result = f"✓ {len(full_text)} chars"
        else:
            new_body = old_body
            result = "— kept short snippet (scrape blocked or paywalled)"

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET body = %s, full_text_fetched = TRUE WHERE id = %s;",
                (new_body, article_id),
            )
        conn.commit()
        print(f"  #{article_id}: {result}")

    except Exception as e:
        # Mark as attempted even on failure, so we don't retry a permanently
        # broken URL forever.
        with conn.cursor() as cur:
            cur.execute("UPDATE articles SET full_text_fetched = TRUE WHERE id = %s;", (article_id,))
        conn.commit()
        print(f"  #{article_id} FAILED: {e}")

    time.sleep(0.5)  # be polite to the sites we're fetching from

conn.close()
print("\nDone.")
