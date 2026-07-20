"""
Pulls the latest articles from each RSS feed and saves new ones to the database.
Run this every 15-30 minutes (we'll automate that later — for now, run it by hand).
"""

import feedparser
import psycopg2
from datetime import datetime, timezone
import os
from dotenv import load_dotenv
from text_utils import clean_text

load_dotenv()  # reads DATABASE_URL from a .env file, see .env.example

# Each source's RSS feed URL. This is the entire "source list" for the MVP —
# adding a source later just means adding one line here.
FEEDS = {
    "BBC": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "NPR": "https://feeds.npr.org/1004/rss.xml",
}


def parse_published_time(entry):
    """
    feedparser gives us the published date as a 'time struct' (a Python-native
    time format), not a normal datetime. This just converts it to something
    Postgres understands. Some entries don't have a date at all, hence the check.
    """
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return None


def get_body(entry):
    """
    Some feeds give the full article body, most only give a summary/snippet.
    We take whatever's available — full body extraction from the article page
    itself is a later improvement, not needed for this first version.
    """
    if hasattr(entry, "content") and entry.content:
        return entry.content[0].value
    return getattr(entry, "summary", "")


def ingest_feed(source_name, feed_url, conn):
    print(f"Fetching {source_name}...")
    parsed = feedparser.parse(feed_url)

    new_count = 0
    for entry in parsed.entries:
        title = clean_text(entry.get("title", "").strip())
        url = entry.get("link", "").strip()
        if not title or not url:
            continue  # skip anything malformed, don't let one bad entry crash the run

        body = clean_text(get_body(entry))
        published_at = parse_published_time(entry)

        with conn.cursor() as cur:
            # ON CONFLICT DO NOTHING is the dedup logic: if this URL is already
            # in the table, Postgres just skips the insert instead of erroring.
            cur.execute(
                """
                INSERT INTO articles (title, body, source, url, published_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                RETURNING id
                """,
                (title, body, source_name, url, published_at),
            )
            if cur.fetchone():
                new_count += 1

    conn.commit()
    print(f"  → {new_count} new articles from {source_name} ({len(parsed.entries)} in feed)")


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        for source_name, feed_url in FEEDS.items():
            try:
                ingest_feed(source_name, feed_url, conn)
            except Exception as e:
                # One feed failing (site down, bad XML) shouldn't stop the others.
                print(f"  ⚠ {source_name} failed: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
