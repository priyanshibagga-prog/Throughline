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

# Hard cutoff: a story whose latest article is older than this never makes
# the paper, no matter how important or well-matched it is — same principle
# as a real newspaper not running yesterday's news just to fill space.
MAX_STORY_AGE_HOURS = 24

# Within that 24-hour window, freshness still matters (breaking news should
# outrank a same-day story from 20 hours ago) — this half-life sets how
# fast that in-window decay happens. Short enough to meaningfully spread
# out a single day, not so short that a story goes stale in an hour.
RECENCY_HALF_LIFE_HOURS = 10

# The edition is built in two sections, like a real front page:
#
# 1. FRONT PAGE — stories important enough that they'd lead any news
#    channel regardless of your topic picks. Always comes first.
# 2. YOUR TOPICS — the rest of the budget. Every story here still has to
#    match one of your selected topics (hard gate, no exceptions), but
#    there's no fixed slot count per topic — they're all ranked together
#    by importance AND your star rating, equally weighted. A thin day
#    for a topic you starred highly doesn't force filler in just to fill
#    a quota; two strong stories from another topic fill that space
#    instead. Your ranking still matters, just not as a hard reservation.

# How important a story has to be to count as front-page news, on its
# own, independent of anyone's topic preferences.
FRONT_PAGE_IMPORTANCE_MIN = 8

# What fraction of the day's reading budget the front page gets before
# the rest goes to your topics. Kept a minority share so the edition
# still reads as personalized, not just "today's biggest news."
FRONT_PAGE_SHARE = 0.25

# Front-page ranking: importance leads, freshness breaks ties.
PRIORITY_IMPORTANCE_WEIGHT = 0.7
PRIORITY_RECENCY_WEIGHT = 0.3

# Topic-section ranking: importance and how much you starred the topic
# count equally — that's the "equally valuable" part. Freshness is a
# lighter factor here since everything's already within the 24h window.
TOPIC_IMPORTANCE_WEIGHT = 0.4
TOPIC_STAR_WEIGHT = 0.4
TOPIC_RECENCY_WEIGHT = 0.2
STAR_SCALE_MAX = 3  # star ratings run 1-3; used to put them on the same 0-10 scale as importance/freshness


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
    """
    Which of the reader's selected topics this story belongs to, for the
    purpose of assigning it to a topic section — the highest-weighted
    topic it touches. Returns (0, <its own first topic>) if it doesn't
    match anything the reader selected.
    """
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


def hours_since(dt):
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def recency_score(hours_old):
    return 10 * math.exp(-hours_old / RECENCY_HALF_LIFE_HOURS)


def priority(importance, fresh, entity_bonus=0):
    """Front-page ranking — importance leads, freshness breaks ties, entity
    match nudges further."""
    return importance * PRIORITY_IMPORTANCE_WEIGHT + fresh * PRIORITY_RECENCY_WEIGHT + min(entity_bonus, 3)


def topic_priority(importance, topic_weight, fresh):
    """
    Topic-section ranking — importance and your star rating for that
    topic count equally, so a 3-star topic with a merely-decent story
    can lose out to a 1-star topic's genuinely important one, and vice
    versa. No per-topic quota: this just ranks every topic-eligible
    candidate on one shared scale.
    """
    star_norm = topic_weight * (10 / STAR_SCALE_MAX)
    return (
        importance * TOPIC_IMPORTANCE_WEIGHT
        + star_norm * TOPIC_STAR_WEIGHT
        + fresh * TOPIC_RECENCY_WEIGHT
    )


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
            SELECT s.id, s.headline, s.summary, s.ai_summary, s.topics, s.historical_context, s.recent_timeline, pe.status
            FROM paper_editions pe
            JOIN stories s ON s.id = pe.story_id
            WHERE pe.user_id = %s AND pe.edition_date = %s
            ORDER BY pe.shown_at;
            """,
            (user_id, edition_date),
        )
        rows = cur.fetchall()

    results = []
    for story_id, headline, summary, ai_summary, topics, historical_context, recent_timeline, status in rows:
        source, art_title, full_text, url = get_representative_article(conn, story_id, user_sources)
        results.append({
            "id": story_id, "headline": headline, "summary": summary, "ai_summary": ai_summary, "topics": topics,
            "status": status, "historical_context": historical_context or [], "recent_timeline": recent_timeline or [],
            "source": source, "full_text": full_text, "url": url,
        })
    return results


def build_todays_paper(conn, user_id, record_edition=True):
    """
    Returns a list of story dicts for this user's edition, and (by default)
    records what was shown in paper_editions so future runs know the history.
    This is the single source of truth both the CLI script and the API use.

    Idempotent per day: once today's edition has been built and recorded,
    later calls (e.g. every page reload) reconstruct that SAME edition
    instead of re-running selection. Re-selecting on every call would
    re-insert paper_editions rows each time, which then count as
    "already shown" and get excluded from the next call's candidate pool
    — a few reloads would visibly burn through the day's fresh stories
    and leave nothing left to show.
    """
    if record_edition:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM paper_editions WHERE user_id = %s AND edition_date = CURRENT_DATE LIMIT 1;",
                (user_id,),
            )
            already_built = cur.fetchone() is not None
        if already_built:
            reading_time_minutes = get_user_profile(conn, user_id)["reading_time_minutes"]
            return get_past_edition(conn, user_id, date.today()), reading_time_minutes

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
                SELECT s.id, s.headline, s.summary, s.ai_summary, s.topics, s.entities, s.importance_score,
                       s.historical_context, s.recent_timeline,
                       MAX(a.published_at) AS latest_update,
                       (SELECT MAX(pe.shown_at) FROM paper_editions pe
                        WHERE pe.story_id = s.id AND pe.user_id = %s) AS last_shown
                FROM stories s
                JOIN articles a ON a.story_id = s.id
                WHERE s.headline IS NOT NULL AND a.source = ANY(%s)
                GROUP BY s.id, s.headline, s.summary, s.ai_summary, s.topics, s.entities, s.importance_score,
                         s.historical_context, s.recent_timeline;
            """, (user_id, user_sources))
        else:
            # No sources selected (or a legacy test user) — don't filter.
            cur.execute("""
                SELECT s.id, s.headline, s.summary, s.ai_summary, s.topics, s.entities, s.importance_score,
                       s.historical_context, s.recent_timeline,
                       MAX(a.published_at) AS latest_update,
                       (SELECT MAX(pe.shown_at) FROM paper_editions pe
                        WHERE pe.story_id = s.id AND pe.user_id = %s) AS last_shown
                FROM stories s
                JOIN articles a ON a.story_id = s.id
                WHERE s.headline IS NOT NULL
                GROUP BY s.id, s.headline, s.summary, s.ai_summary, s.topics, s.entities, s.importance_score,
                         s.historical_context, s.recent_timeline;
            """, (user_id,))
        stories = cur.fetchall()

    candidates = []
    for (story_id, headline, summary, ai_summary, topics, entities, importance,
         historical_context, recent_timeline, latest_update, last_shown) in stories:
        if last_shown is None:
            status = "new"
        elif latest_update > last_shown:
            status = "update"
        else:
            continue

        # Hard freshness cutoff — nothing older than a day makes the
        # paper, period, regardless of how important or well-matched it
        # is. If that leaves fewer than the target story count, the
        # edition just runs thinner; we never backfill with stale news.
        hours_old = hours_since(latest_update)
        if hours_old > MAX_STORY_AGE_HOURS:
            continue

        importance = importance or 0
        fresh = recency_score(hours_old)
        topic_weight, bucket = best_matching_topic(topics, topic_weights)

        candidates.append({
            "id": story_id, "headline": headline, "summary": summary, "ai_summary": ai_summary,
            "topics": topics, "entities": entities, "importance": importance, "fresh": fresh,
            "bucket": bucket, "topic_weight": topic_weight, "status": status,
            "priority": priority(importance, fresh, entity_boost(entities, entity_preferences)),
            "topic_priority": topic_priority(importance, topic_weight, fresh),
            "historical_context": historical_context or [],
            "recent_timeline": recent_timeline or [],
        })

    story_count = max(1, round(reading_time_minutes * STORIES_PER_MINUTE))

    # Front page — genuinely important stories, independent of topic
    # picks. Comes first and takes a minority share of the budget so the
    # rest of the edition still reads as personalized.
    front_page_pool = sorted(
        (c for c in candidates if c["importance"] >= FRONT_PAGE_IMPORTANCE_MIN),
        key=lambda c: c["priority"], reverse=True,
    )
    front_page_target = max(1, round(story_count * FRONT_PAGE_SHARE)) if front_page_pool else 0
    selected = front_page_pool[:front_page_target]
    selected_ids = {c["id"] for c in selected}

    # Your topics — whatever's left of the budget. Every candidate here
    # still has to match one of your selected topics, but they're all
    # ranked together on one shared scale (see topic_priority) rather
    # than sliced into fixed per-topic quotas — a thin day for one topic
    # just means another one fills more of the space instead of filler.
    remaining_slots = story_count - len(selected)
    topic_pool = sorted(
        (c for c in candidates if c["id"] not in selected_ids and c["topic_weight"] > 0),
        key=lambda c: c["topic_priority"], reverse=True,
    )
    selected.extend(topic_pool[:remaining_slots])

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


def print_edition(user_id, selected, reading_time_minutes):
    print(f"--- User {user_id}: today's {reading_time_minutes}-minute edition ({len(selected)} stories) ---\n")
    for item in selected:
        print(f"[front page {item['priority']:.1f} | topic {item['topic_priority']:.1f}] ({item['bucket']}) [{item['status'].upper()}] {item['headline']}")
        print(f"   entities: {item['entities']}  |  topic weight: {item['topic_weight']}")
        print(f"   importance: {item['importance']}  |  freshness: {item['fresh']:.1f}")
        print(f"   {item['summary']}\n")


def main():
    import sys
    conn = psycopg2.connect(os.environ["DATABASE_URL"])

    if len(sys.argv) > 1:
        # Debug mode: python3 build_paper.py <user_id> — builds and
        # prints the full breakdown for just this one user.
        user_id = int(sys.argv[1])
        selected, reading_time_minutes = build_todays_paper(conn, user_id)
        print_edition(user_id, selected, reading_time_minutes)
    else:
        # Pipeline mode: python3 build_paper.py — builds today's edition
        # for EVERY user, recording their paper_editions selections. This
        # has to run before generate_context.py, which only generates
        # historical/recent/AI-summary content for stories that actually
        # got selected into someone's edition today — not the entire
        # backlog of synthesized stories.
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users ORDER BY id;")
            user_ids = [row[0] for row in cur.fetchall()]

        for user_id in user_ids:
            try:
                selected, reading_time_minutes = build_todays_paper(conn, user_id)
                print(f"User {user_id}: {len(selected)} stories selected for today's edition")
            except Exception as e:
                print(f"User {user_id} FAILED: {e}")

    conn.close()


if __name__ == "__main__":
    main()
