"""
Groups articles into stories based on how similar their embeddings are.

For each unclustered article, we check: is it close enough to an existing
recent story to be "the same event"? If yes, join that story. If no,
start a new one. This is the nearest-centroid logic we talked through.
"""

import os
import numpy as np
import psycopg2
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv

load_dotenv()

# How similar two articles' meanings need to be to count as "the same story."
# 1.0 = identical meaning, 0.0 = completely unrelated. This is a starting
# guess — after running this, we'll look at real results and tune it.
SIMILARITY_THRESHOLD = 0.55

# Only compare against stories created recently — a week-old story shouldn't
# silently absorb an unrelated article just because the topic is similar.
LOOKBACK_HOURS = 48


def to_numpy(x):
    # pgvector hands back a custom Vector object, not a plain array — this
    # unwraps it regardless of which version's API we're dealing with.
    if hasattr(x, "to_numpy"):
        return x.to_numpy()
    if hasattr(x, "to_list"):
        return np.array(x.to_list(), dtype=float)
    return np.array(x, dtype=float)


def cosine_similarity(a, b):
    a = to_numpy(a)
    b = to_numpy(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    register_vector(conn)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, title, embedding FROM articles
            WHERE embedding IS NOT NULL AND story_id IS NULL
            ORDER BY published_at ASC;
        """)
        unclustered = cur.fetchall()

    print(f"Clustering {len(unclustered)} articles...\n")

    for article_id, title, embedding in unclustered:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, centroid_embedding FROM stories
                WHERE created_at > now() - interval '{LOOKBACK_HOURS} hours';
            """)
            candidate_stories = cur.fetchall()

        best_story_id = None
        best_similarity = 0.0

        for story_id, centroid in candidate_stories:
            sim = cosine_similarity(embedding, centroid)
            if sim > best_similarity:
                best_similarity = sim
                best_story_id = story_id

        if best_story_id is not None and best_similarity >= SIMILARITY_THRESHOLD:
            with conn.cursor() as cur:
                cur.execute("UPDATE articles SET story_id = %s WHERE id = %s;", (best_story_id, article_id))
                # Recompute the centroid as the average of every article now in this story.
                cur.execute("""
                    UPDATE stories SET
                        centroid_embedding = (SELECT AVG(embedding) FROM articles WHERE story_id = %s),
                        updated_at = now()
                    WHERE id = %s;
                """, (best_story_id, best_story_id))
            print(f"  joined story #{best_story_id} (similarity {best_similarity:.2f}) — {title[:65]}")
        else:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO stories (centroid_embedding) VALUES (%s) RETURNING id;", (embedding,))
                new_story_id = cur.fetchone()[0]
                cur.execute("UPDATE articles SET story_id = %s WHERE id = %s;", (new_story_id, article_id))
            print(f"  NEW story #{new_story_id} — {title[:65]}")

    conn.commit()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
