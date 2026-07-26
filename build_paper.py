"""
Builds today's personalized paper for a given user.

Can be run directly (python3 build_paper.py <user_id>) or imported and
called from the API (build_todays_paper), which is what actually renders
the frontend.
"""

import os
import math
import psycopg2
from datetime import datetime, date, timezone
from dotenv import load_dotenv

load_dotenv()

STORIES_PER_MINUTE = 0.2
RECENCY_HALF_LIFE_HOURS = 24
BREAKING_IMPORTANCE_MIN = 8
BREAKING_FRESHNESS_MIN = 6.0


def get_user_profile(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sources, topic_weights, entity_preferences, reading_time_minutes FROM users WHERE id = %s;",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"No user with id {user_id} — run create_test_user.py first.")
    sources, topic_weights, entity_preferences, reading_time_minutes = row
    return {
        "sources": sources or [],
        "topic_weights": topic_weights or {},
        "entity_preferences": entity_preferences or {},
        "reading_time_minutes": reading_time_minutes,
    }


def best_matching_topic(story_topics, topic_weights):
    if not story_topics:
        return 0, "Other"
    best_topic = max(story_topics, key=lambda t: topic_weights.get(t, 0))
    match_score = topic_weights.get(best_topic, 0)
    if match_score == 0:
        return 0, story_topics[0]
    return match_score, best_topic


def entity_boost(story_entities, entity_preferences):
    if not story_entities or not entity_preferences:
        return 0
    total = 0
    for entity in story_entities:
        entity_lower = entity.lower()
        for pref_keyword, weight in entity_preferences.items():
            if pref_keyword.lower() in entity_lower:
                total += weight
    return total


def recency_score(latest_update):
    hours_old = (datetime.now(timezone.utc) - latest_update).total_seconds() / 3600
    return 10 * math.exp(-hours_old / RECENCY_HALF_LIFE_HOURS)


import trafilatura
from text_utils import clean_text


def get_representative_article(conn, story_id, allowed_sources=None):
    """
    Picks the longest article in this story's cluster (optionally only
    among the user's selected sources) and fetches its REAL full text on
    demand — cached after the first time via full_text_fetched.
    """
    with conn.cursor() as cur:
        if allowed_sources:
            cur.execute(
                "SELECT id, source, title, body, url, full_text_fetched FROM articles WHERE story_id = %s AND source = ANY(%s) ORDER BY LENGTH(body) DESC LIMIT 1;",
                (story_id, allowed_sources),
            )
        else:
            cur.execute(
                "SELECT id, source, title, body, url, full_text_fetched FROM articles WHERE story_id = %s ORDER BY LENGTH(body) DESC LIMIT 1;",
                (story_id,),
            )
        row = cur.fetchone()
    if not row:
        return None, None, None, None

    article_id, source, title, body, url, full_text_fetched = row

    if not full_text_fetched:
        try:
            downloaded = trafilatura.fetch_url(url)
            full_text = trafilatura.extract(downloaded) if downloaded else None
            full_text = clean_text(full_text) if full_text else None
            if full_text and len(full_text) > len(body or ""):
                body = full_text
            with conn.cursor() as cur:
                cur.execute("UPDATE articles SET body = %s, full_text_fetched = TRUE WHERE id = %s;", (body, article_id))
            conn.commit()
        except Exception:
            with conn.cursor() as cur:
                cur.execute("UPDATE articles SET full_text_fetched = TRUE WHERE id = %s;", (article_id,))
            conn.commit()

    return source, title, body, url


def get_available_editions(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT edition_date FROM paper_editions WHERE user_id = %s ORDER BY edition_date DESC;",
            (user_id,),
        )
        return [row[0].isoformat() for row in cur.fetchall()]


def get_past_edition(conn, user_id, edition_date):
    """
    Reconstructs what was actually shown to this user on a past date —
    read-only, does not rescore or record anything new.
    """
    user_sources = get_user_profile(conn, user_id)["sources"]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.headline, s.summary, s.topics, s.historical_context, s.recent_timeline, pe.status
            FROM paper_editions pe
            JOIN stories s ON s.id = pe.story_id
            WHERE pe.user_id = %s AND pe.edition_date = %s
            ORDER BY pe.shown_at;
            """,
            (user_id, edition_date),
        )
        rows = cur.fetchall()

    results = []
    for story_id, headline, summary, topics, historical_context, recent_timeline, status in rows:
        source, art_title, full_text, url = get_representative_article(conn, story_id, user_sources)
        results.append({
            "id": story_id, "headline": headline, "summary": summary, "topics": topics,
            "status": status, "historical_context": historical_context or [], "recent_timeline": recent_timeline or [],
            "source": source, "full_text": full_text, "url": url,
        })
    return results


def build_todays_paper(conn, user_id, record_edition=True):
    """
    Returns a list of story dicts for this user's edition, and (by default)
    records what was shown in paper_editions so future runs know the history.
    This is the single source of truth both the CLI script and the API use.
    """
    user = get_user_profile(conn, user_id)
    user_sources = user["sources"]
    topic_weights = user["topic_weights"]
    entity_preferences = user["entity_preferences"]
    reading_time_minutes = user["reading_time_minutes"]

    with conn.cursor() as cur:
        if user_sources:
            # Only consider articles from sources this user actually
            # selected — a story with no matching source is excluded
            # entirely, since it never appears anywhere in the JOIN.
            cur.execute("""
                SELECT s.id, s.headline, s.summary, s.topics, s.entities, s.importance_score,
                       s.historical_context, s.recent_timeline,
                       MAX(a.published_at) AS latest_update,
                       (SELECT MAX(pe.shown_at) FROM paper_editions pe
                        WHERE pe.story_id = s.id AND pe.user_id = %s) AS last_shown
                FROM stories s
                JOIN articles a ON a.story_id = s.id
                WHERE s.headline IS NOT NULL AND a.source = ANY(%s)
                GROUP BY s.id, s.headline, s.summary, s.topics, s.entities, s.importance_score,
                         s.historical_context, s.recent_timeline;
            """, (user_id, user_sources))
        else:
            # No sources selected (or a legacy test user) — don't filter.
            cur.execute("""
                SELECT s.id, s.headline, s.summary, s.topics, s.entities, s.importance_score,
                       s.historical_context, s.recent_timeline,
                       MAX(a.published_at) AS latest_update,
                       (SELECT MAX(pe.shown_at) FROM paper_editions pe
                        WHERE pe.story_id = s.id AND pe.user_id = %s) AS last_shown
                FROM stories s
                JOIN articles a ON a.story_id = s.id
                WHERE s.headline IS NOT NULL
                GROUP BY s.id, s.headline, s.summary, s.topics, s.entities, s.importance_score,
                         s.historical_context, s.recent_timeline;
            """, (user_id,))
        stories = cur.fetchall()

    candidates = []
    for (story_id, headline, summary, topics, entities, importance,
         historical_context, recent_timeline, latest_update, last_shown) in stories:
        if last_shown is None:
            status = "new"
        elif latest_update > last_shown:
            status = "update"
        else:
            continue

        topic_score, bucket = best_matching_topic(topics, topic_weights)
        fresh = recency_score(latest_update)
        e_boost = entity_boost(entities, entity_preferences)
        score = (fresh * 3) + (importance * 2) + (e_boost * 1.5) + (topic_score * 1)

        candidates.append({
            "score": score, "bucket": bucket, "id": story_id, "headline": headline,
            "summary": summary, "topics": topics, "entities": entities,
            "importance": importance, "fresh": fresh, "e_boost": e_boost, "status": status,
            "historical_context": historical_context or [],
            "recent_timeline": recent_timeline or [],
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)

    story_count = max(1, round(reading_time_minutes * STORIES_PER_MINUTE))
    selected = []
    used_buckets = set()

    for item in candidates:
        if len(selected) >= story_count:
            break
        if item["bucket"] not in used_buckets:
            selected.append(item)
            used_buckets.add(item["bucket"])

    if len(selected) < story_count:
        chosen_ids = {item["id"] for item in selected}
        for item in candidates:
            if len(selected) >= story_count:
                break
            if item["id"] in chosen_ids:
                continue
            is_breaking = item["importance"] >= BREAKING_IMPORTANCE_MIN and item["fresh"] >= BREAKING_FRESHNESS_MIN
            if item["bucket"] not in used_buckets or is_breaking:
                selected.append(item)
                chosen_ids.add(item["id"])
                used_buckets.add(item["bucket"])

    for item in selected:
        source, art_title, full_text, url = get_representative_article(conn, item["id"], user_sources)
        item["source"] = source
        item["full_text"] = full_text
        item["url"] = url

    if record_edition:
        with conn.cursor() as cur:
            for item in selected:
                cur.execute(
                    "INSERT INTO paper_editions (user_id, edition_date, story_id, status) VALUES (%s, %s, %s, %s);",
                    (user_id, date.today(), item["id"], item["status"]),
                )
        conn.commit()

    return selected, reading_time_minutes


def main():
    import sys
    user_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    selected, reading_time_minutes = build_todays_paper(conn, user_id)
    conn.close()

    print(f"--- Today's {reading_time_minutes}-minute edition ({len(selected)} stories) ---\n")
    for item in selected:
        print(f"[{item['score']:.1f}] ({item['bucket']}) [{item['status'].upper()}] {item['headline']}")
        print(f"   entities: {item['entities']}  |  entity boost: {item['e_boost']}")
        print(f"   importance: {item['importance']}  |  freshness: {item['fresh']:.1f}")
        print(f"   {item['summary']}\n")


if __name__ == "__main__":
    main()
