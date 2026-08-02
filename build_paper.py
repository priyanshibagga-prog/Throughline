"""
Builds today's personalized paper for a given user.

Can be run directly (python3 build_paper.py <user_id>) or imported and
called from the API (build_todays_paper), which is what actually renders
the frontend.
"""

import os
import math
import subprocess
import sys
import numpy as np
import psycopg2
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv
from pgvector.psycopg2 import register_vector

load_dotenv()

# Arbitrary fixed key for a Postgres advisory lock — see
# ensure_todays_data_is_fresh. Any int works; it just needs to be the
# same constant every time so concurrent callers contend on it.
PIPELINE_LOCK_KEY = 848215001

STORIES_PER_MINUTE = 0.2

# Ceiling: a story whose latest article is older than this never makes the
# paper no matter what, so a pipeline outage doesn't suddenly flood an
# edition with days of backlog once it's back up.
MAX_STORY_AGE_HOURS = 24

# Within that 24-hour window, freshness still matters (breaking news should
# outrank a same-day story from 20 hours ago) — this half-life sets how
# fast that in-window decay happens. Short enough to meaningfully spread
# out a single day, not so short that a story goes stale in an hour.
RECENCY_HALF_LIFE_HOURS = 10

# The edition is built in four passes, like a real front page:
#
# 1. FRONT PAGE — stories important enough that they'd lead any news
#    channel regardless of your topic picks. Always comes first.
# 2. TOP-STARRED GUARANTEE — each topic you starred at the max gets one
#    slot, but only if it has at least a reasonably important story.
# 3. YOUR TOPICS — whatever's left of the budget. Every story here still
#    has to match one of your selected topics (hard gate, no exceptions),
#    but there's no fixed slot count per topic — they're all ranked
#    together by importance AND your star rating, equally weighted. A
#    thin day for a topic doesn't force filler in; another topic you
#    also care about fills that space instead.
# 4. CATCH-ALL — if the budget still isn't full (e.g. narrow topic
#    picks with little fresh coverage today), fill the rest with the
#    next-best stories overall, no topic gate. Reading time should
#    drive story count regardless of how many topics you picked —
#    someone with one topic selected shouldn't get a visibly thinner
#    paper than someone with ten.
#
# Throughout all four, a story is skipped if it's a near-duplicate of
# something already picked today or shown to you before — clustering
# doesn't always merge two stories covering the same event, so this
# catches what it misses.

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

# A topic starred at the max still isn't guaranteed a slot under pure
# ranking alone (front-page news or other topics could numerically win
# every slot). This guarantees one, but only if there's at least a
# reasonably important story for it — NOT "whatever's there," which is
# what caused low-importance filler to get forced in before.
TOP_STAR_WEIGHT = 3
TOP_STAR_IMPORTANCE_MIN = 5

# Clustering (cluster.py) groups articles into stories by embedding
# similarity, but it doesn't always merge two stories that are really
# the same event — a follow-up angle can drift just far enough to land
# in its own cluster. Reusing cluster.py's own "same event" bar here
# stops those near-duplicates from landing in the same edition, AND from
# ever resurfacing on a later day just because clustering gave the same
# story a fresh id — this checks against everything ever shown to the
# user, not just a recent window. (A story that's genuinely the SAME
# story, still updating, is a separate case handled by last_shown/status
# below — that's an intentional "here's what's new" re-show, not this.)
DUPLICATE_SIMILARITY_THRESHOLD = 0.55


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


def to_numpy(x):
    # pgvector hands back a custom Vector object, not a plain array —
    # this unwraps it regardless of which version's API we're dealing
    # with (same helper as cluster.py, which is what produced these).
    if x is None:
        return None
    if hasattr(x, "to_numpy"):
        return x.to_numpy()
    if hasattr(x, "to_list"):
        return np.array(x.to_list(), dtype=float)
    return np.array(x, dtype=float)


def cosine_similarity(a, b):
    a, b = to_numpy(a), to_numpy(b)
    if a is None or b is None:
        return 0.0
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom else 0.0


def is_near_duplicate(candidate, taken_refs):
    """
    True if `candidate` is close enough to a DIFFERENT story already in
    `taken_refs` (a list of (story_id, centroid_embedding) pairs) to be
    covering the same event — same bar clustering itself uses, applied
    here to catch the cases clustering missed.
    """
    centroid = candidate.get("centroid_embedding")
    if centroid is None:
        return False
    for other_id, other_centroid in taken_refs:
        if other_id == candidate["id"]:
            continue
        if cosine_similarity(centroid, other_centroid) >= DUPLICATE_SIMILARITY_THRESHOLD:
            return True
    return False


def pick_without_duplicates(pool, limit, taken_refs):
    """
    Greedily takes up to `limit` stories from `pool` (assumed sorted
    best-first), skipping any near-duplicate of something already in
    taken_refs — and appending each pick to taken_refs, so later calls
    in the same edition build (and later picks within this same call)
    also avoid duplicating it.
    """
    chosen = []
    for c in pool:
        if len(chosen) >= limit:
            break
        if is_near_duplicate(c, taken_refs):
            continue
        chosen.append(c)
        taken_refs.append((c["id"], c.get("centroid_embedding")))
    return chosen


def run_pipeline_step(script_name):
    """
    Runs one of the standalone pipeline scripts (ingest.py, embed_all.py,
    etc.) as a subprocess, same as run_pipeline.sh does — reused here so
    the exact same scripts back both the manual/scheduled path and the
    on-demand one. Best-effort: logs and moves on rather than raising, so
    one failing step (e.g. a down RSS feed, an exhausted LLM quota)
    doesn't turn into a 500 for whichever visitor happened to trigger it.
    """
    project_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        [sys.executable, script_name],
        cwd=project_dir,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[pipeline] {script_name} exited {result.returncode}:\n{result.stderr[-2000:]}")


def ensure_todays_data_is_fresh(conn):
    """
    Runs the shared ingest -> embed -> cluster -> synthesize refresh
    once per day — the first time ANYONE's edition is built that day,
    not on a fixed schedule and not once per user. A Postgres advisory
    lock keeps two near-simultaneous first-visits-of-the-day from both
    kicking it off at once; whichever loses the race just waits for the
    winner to finish, then finds pipeline_runs already marked done.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT completed_at FROM pipeline_runs WHERE run_date = CURRENT_DATE;")
        row = cur.fetchone()
    if row and row[0] is not None:
        return

    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s);", (PIPELINE_LOCK_KEY,))
    try:
        # Re-check now that we hold the lock — another request may have
        # already finished the run while we were waiting for it.
        with conn.cursor() as cur:
            cur.execute("SELECT completed_at FROM pipeline_runs WHERE run_date = CURRENT_DATE;")
            row = cur.fetchone()
        if row and row[0] is not None:
            return

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_runs (run_date) VALUES (CURRENT_DATE) ON CONFLICT (run_date) DO NOTHING;"
            )
        conn.commit()

        for script in ("ingest.py", "embed_all.py", "cluster.py", "synthesize_all.py"):
            run_pipeline_step(script)

        with conn.cursor() as cur:
            cur.execute("UPDATE pipeline_runs SET completed_at = now() WHERE run_date = CURRENT_DATE;")
        conn.commit()
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s);", (PIPELINE_LOCK_KEY,))


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

        # No edition yet today for anyone we've built for so far — make
        # sure the shared story pool is actually fresh before picking
        # from it. Only runs once per day total (see the function).
        ensure_todays_data_is_fresh(conn)

    register_vector(conn)  # so centroid_embedding columns below come back as usable vectors, not raw text
    user = get_user_profile(conn, user_id)
    user_sources = user["sources"]
    topic_weights = user["topic_weights"]
    entity_preferences = user["entity_preferences"]
    reading_time_minutes = user["reading_time_minutes"]

    # Freshness cutoff: articles have to be new since THIS user's last
    # edition was actually built, not just "within the last 24 hours" —
    # a rolling 24h window can still include an article that was already
    # sitting there as a candidate last time we built their edition, even
    # though nothing about it is actually new. MAX_STORY_AGE_HOURS stays
    # as a ceiling (never reach further back than a day, even if their
    # last edition was built longer ago than that — a paused pipeline
    # shouldn't dump days of backlog into one edition).
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(shown_at) FROM paper_editions WHERE user_id = %s AND edition_date < CURRENT_DATE;",
            (user_id,),
        )
        last_edition_time = cur.fetchone()[0]
    rolling_cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_STORY_AGE_HOURS)
    freshness_cutoff = max(last_edition_time, rolling_cutoff) if last_edition_time else rolling_cutoff

    with conn.cursor() as cur:
        if user_sources:
            # Only consider articles from sources this user actually
            # selected — a story with no matching source is excluded
            # entirely, since it never appears anywhere in the JOIN.
            cur.execute("""
                SELECT s.id, s.headline, s.summary, s.ai_summary, s.topics, s.entities, s.importance_score,
                       s.historical_context, s.recent_timeline, s.centroid_embedding,
                       MAX(a.published_at) AS latest_update,
                       (SELECT MAX(pe.shown_at) FROM paper_editions pe
                        WHERE pe.story_id = s.id AND pe.user_id = %s) AS last_shown
                FROM stories s
                JOIN articles a ON a.story_id = s.id
                WHERE s.headline IS NOT NULL AND a.source = ANY(%s)
                GROUP BY s.id, s.headline, s.summary, s.ai_summary, s.topics, s.entities, s.importance_score,
                         s.historical_context, s.recent_timeline, s.centroid_embedding;
            """, (user_id, user_sources))
        else:
            # No sources selected (or a legacy test user) — don't filter.
            cur.execute("""
                SELECT s.id, s.headline, s.summary, s.ai_summary, s.topics, s.entities, s.importance_score,
                       s.historical_context, s.recent_timeline, s.centroid_embedding,
                       MAX(a.published_at) AS latest_update,
                       (SELECT MAX(pe.shown_at) FROM paper_editions pe
                        WHERE pe.story_id = s.id AND pe.user_id = %s) AS last_shown
                FROM stories s
                JOIN articles a ON a.story_id = s.id
                WHERE s.headline IS NOT NULL
                GROUP BY s.id, s.headline, s.summary, s.ai_summary, s.topics, s.entities, s.importance_score,
                         s.historical_context, s.recent_timeline, s.centroid_embedding;
            """, (user_id,))
        stories = cur.fetchall()

    candidates = []
    for (story_id, headline, summary, ai_summary, topics, entities, importance,
         historical_context, recent_timeline, centroid_embedding, latest_update, last_shown) in stories:
        if last_shown is None:
            status = "new"
        elif latest_update > last_shown:
            status = "update"
        else:
            continue

        # Nothing older than freshness_cutoff makes the paper — genuinely
        # new since this user's last edition, capped at a day old either
        # way. If that leaves fewer than the target story count, the
        # edition just runs thinner; we never backfill with stale news.
        if latest_update <= freshness_cutoff:
            continue
        hours_old = hours_since(latest_update)

        importance = importance or 0
        fresh = recency_score(hours_old)
        topic_weight, bucket = best_matching_topic(topics, topic_weights)

        candidates.append({
            "id": story_id, "headline": headline, "summary": summary, "ai_summary": ai_summary,
            "topics": topics, "entities": entities, "importance": importance, "fresh": fresh,
            "bucket": bucket, "topic_weight": topic_weight, "status": status,
            "centroid_embedding": centroid_embedding,
            "priority": priority(importance, fresh, entity_boost(entities, entity_preferences)),
            "topic_priority": topic_priority(importance, topic_weight, fresh),
            "historical_context": historical_context or [],
            "recent_timeline": recent_timeline or [],
        })

    story_count = max(1, round(reading_time_minutes * STORIES_PER_MINUTE))

    # Every story ever shown to this user, on any past day — seeds the
    # duplicate check below so a story that's really the same event as
    # something already shown doesn't reappear just because clustering
    # gave it a different story id. (Same-story updates are unaffected —
    # is_near_duplicate skips comparisons against the same story id.)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT s.id, s.centroid_embedding
            FROM paper_editions pe
            JOIN stories s ON s.id = pe.story_id
            WHERE pe.user_id = %s AND pe.edition_date < CURRENT_DATE;
        """, (user_id,))
        taken_refs = [(row[0], row[1]) for row in cur.fetchall()]

    # Front page — genuinely important stories, independent of topic
    # picks. Comes first and takes a minority share of the budget so the
    # rest of the edition still reads as personalized.
    front_page_pool = sorted(
        (c for c in candidates if c["importance"] >= FRONT_PAGE_IMPORTANCE_MIN),
        key=lambda c: c["priority"], reverse=True,
    )
    front_page_target = max(1, round(story_count * FRONT_PAGE_SHARE)) if front_page_pool else 0
    selected = pick_without_duplicates(front_page_pool, front_page_target, taken_refs)
    selected_ids = {c["id"] for c in selected}

    # A topic you starred at the max gets one guaranteed slot if it has
    # at least one reasonably important story (>= 5) — not a fixed quota
    # regardless of quality, just a floor so your top priority can't get
    # entirely crowded out by front-page news or other topics on a day
    # when nothing spectacular is happening in it.
    for topic, weight in topic_weights.items():
        if weight != TOP_STAR_WEIGHT:
            continue
        pool = sorted(
            (c for c in candidates
             if c["id"] not in selected_ids and c["bucket"] == topic and c["importance"] >= TOP_STAR_IMPORTANCE_MIN),
            key=lambda c: c["topic_priority"], reverse=True,
        )
        guaranteed = pick_without_duplicates(pool, 1, taken_refs)
        selected.extend(guaranteed)
        selected_ids.update(c["id"] for c in guaranteed)

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
    selected.extend(pick_without_duplicates(topic_pool, remaining_slots, taken_refs))
    selected_ids.update(c["id"] for c in selected)

    # Catch-all — reading time still drives story count even for a
    # reader with narrow topic picks (someone who only selected one
    # topic shouldn't get a noticeably thinner paper than someone who
    # selected ten). If topic-matched content runs out before the
    # budget does, fill the rest with the next-best fresh, non-duplicate
    # stories overall, same importance/freshness ranking as the front
    # page — just not gated to their topics anymore. Freshness, dedup,
    # and "not already shown" still apply; only the topic-match
    # requirement is dropped, and only once everything else is exhausted.
    still_needed = story_count - len(selected)
    if still_needed > 0:
        catch_all_pool = sorted(
            (c for c in candidates if c["id"] not in selected_ids),
            key=lambda c: c["priority"], reverse=True,
        )
        selected.extend(pick_without_duplicates(catch_all_pool, still_needed, taken_refs))

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

        # Generate AI summary / historical context / recent timeline for
        # whichever of today's selections don't have it yet. Scoped by
        # generate_context.py's own query to today's paper_editions, so
        # this is a fast no-op if an earlier visitor today already
        # triggered it for the same stories.
        run_pipeline_step("generate_context.py")

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
