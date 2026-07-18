"""
Picks and orders today's stories for ONE user, based on THAT user's own
profile (see get_test_user_profile — test data, not a system default).
Also tracks which stories have been shown before, so the same unchanged
story doesn't repeat once nothing new has happened on it.
"""

import os
import math
import psycopg2
from datetime import datetime, date, timezone
from dotenv import load_dotenv

load_dotenv()


def get_test_user_profile():
    return {
        "topic_weights": {
            "Middle East": 2,
            "Technology": 2,
            "World": 1,
            "Headlines": 1,
        },
        "entity_preferences": {},
        "reading_time_minutes": 30,
    }


STORIES_PER_MINUTE = 0.2
RECENCY_HALF_LIFE_HOURS = 24
BREAKING_IMPORTANCE_MIN = 8
BREAKING_FRESHNESS_MIN = 6.0


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


def main():
    user = get_test_user_profile()
    topic_weights = user["topic_weights"]
    entity_preferences = user["entity_preferences"]
    reading_time_minutes = user["reading_time_minutes"]

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.id, s.headline, s.summary, s.topics, s.entities, s.importance_score,
                   MAX(a.published_at) AS latest_update,
                   (SELECT MAX(pe.shown_at) FROM paper_editions pe WHERE pe.story_id = s.id) AS last_shown
            FROM stories s
            JOIN articles a ON a.story_id = s.id
            WHERE s.headline IS NOT NULL
            GROUP BY s.id, s.headline, s.summary, s.topics, s.entities, s.importance_score;
        """)
        stories = cur.fetchall()

    candidates = []
    for story_id, headline, summary, topics, entities, importance, latest_update, last_shown in stories:
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

    print(f"--- Today's {reading_time_minutes}-minute edition ({len(selected)} stories) ---\n")
    for item in selected:
        print(f"[{item['score']:.1f}] ({item['bucket']}) [{item['status'].upper()}] {item['headline']}")
        print(f"   entities: {item['entities']}  |  entity boost: {item['e_boost']}")
        print(f"   importance: {item['importance']}  |  freshness: {item['fresh']:.1f}")
        print(f"   {item['summary']}\n")

    with conn.cursor() as cur:
        for item in selected:
            cur.execute(
                "INSERT INTO paper_editions (edition_date, story_id, status) VALUES (%s, %s, %s);",
                (date.today(), item["id"], item["status"]),
            )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
