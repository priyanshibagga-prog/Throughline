"""
The API the frontend actually talks to.

Run with:
    uvicorn api:app --reload

Then visit http://localhost:8000/docs to see and test every endpoint
in a browser — FastAPI generates that automatically.
"""

import os
import json
import threading
import psycopg2
from datetime import date as date_cls
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from build_paper import build_todays_paper, get_past_edition, get_available_editions, get_user_profile, ensure_todays_data_is_fresh

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set on the deployed backend (not in local .env) to enable GET /api/admin/warm
# — a free stand-in for a paid scheduled job. A GitHub Actions workflow pings
# this once a day with the matching key, so the shared story pool is usually
# already warm before any real visitor shows up (see .github/workflows).
# Unset (the local-dev default) means the endpoint always rejects.
ADMIN_TRIGGER_KEY = os.environ.get("ADMIN_TRIGGER_KEY")


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


@app.get("/api/paper/{user_id}")
def get_paper(user_id: int, edition_date: Optional[str] = None):
    """
    Returns a personalized edition for this user. With no edition_date,
    returns (and records) TODAY's edition. With a past date, reconstructs
    exactly what was shown that day — read-only, no rescoring.
    """
    conn = get_conn()
    try:
        today_str = date_cls.today().isoformat()
        if edition_date is None or edition_date == today_str:
            stories, reading_time_minutes = build_todays_paper(conn, user_id)
        else:
            stories = get_past_edition(conn, user_id, edition_date)
            reading_time_minutes = get_user_profile(conn, user_id)["reading_time_minutes"]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        conn.close()

    return {
        "reading_time_minutes": reading_time_minutes,
        "story_count": len(stories),
        "stories": [
            {
                "id": item["id"],
                "headline": item["headline"],
                "summary": item["summary"],
                "ai_summary": item.get("ai_summary"),
                "topics": item["topics"],
                "status": item["status"],
                "historical_context": item["historical_context"],
                "recent_timeline": item["recent_timeline"],
                "source": item.get("source"),
                "full_text": item.get("full_text"),
                "url": item.get("url"),
            }
            for item in stories
        ],
    }


@app.get("/api/editions/{user_id}")
def get_editions(user_id: int):
    """Every date this user has an edition for — populates the date dropdown."""
    conn = get_conn()
    try:
        dates = get_available_editions(conn, user_id)
    finally:
        conn.close()
    return {"dates": dates}


@app.get("/api/sources")
def get_sources():
    """The fixed list of sources — for the onboarding sources screen."""
    return ["BBC", "Al Jazeera", "The Guardian", "NPR"]


@app.get("/api/topics")
def get_topics():
    """The fixed topic list — for the onboarding topics screen."""
    return [
        "Headlines", "World", "U.S. News", "Politics", "Middle East", "Europe",
        "Asia", "Africa", "Americas", "Technology", "Business & Economy",
        "Markets & Finance", "Science", "Health", "Climate & Environment",
        "Culture", "Arts & Books", "Film & TV", "Sports", "Opinion & Analysis",
        "Education", "Travel", "Food & Drink",
    ]


class UserCreate(BaseModel):
    email: str
    sources: list[str]
    topic_weights: dict
    reading_time_minutes: int = 30


@app.post("/api/users")
def create_user(payload: UserCreate):
    """
    Creates (or updates) a user from the onboarding flow — this is what
    the sources + topics + reading-time screens actually call now, and
    what the account-menu settings screens call to save an edit. Callers
    always send the full profile (edits merge onto the current one
    client-side first), so this can safely overwrite every column.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (email, sources, topic_weights, reading_time_minutes)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    sources = EXCLUDED.sources,
                    topic_weights = EXCLUDED.topic_weights,
                    reading_time_minutes = EXCLUDED.reading_time_minutes
                RETURNING id;
                """,
                (payload.email, payload.sources, json.dumps(payload.topic_weights), payload.reading_time_minutes),
            )
            user_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    return {"user_id": user_id}


@app.get("/api/admin/warm")
def warm_pipeline(key: str = ""):
    """
    Kicks off the shared ingest/embed/cluster/synthesize refresh for
    today, if it hasn't run yet — meant to be pinged once a day by a
    scheduler (see .github/workflows/daily-warm.yml) instead of paying
    for a platform-native cron job. Runs in a background thread and
    returns immediately: the refresh can take minutes on a heavy news
    day, far longer than we want to hold an HTTP request open for.
    """
    if not ADMIN_TRIGGER_KEY or key != ADMIN_TRIGGER_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing key")

    def run():
        conn = get_conn()
        try:
            ensure_todays_data_is_fresh(conn)
        finally:
            conn.close()

    threading.Thread(target=run, daemon=True).start()
    return {"status": "triggered"}
