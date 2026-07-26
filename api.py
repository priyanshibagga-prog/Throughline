"""
The API the frontend actually talks to.

Run with:
    uvicorn api:app --reload

Then visit http://localhost:8000/docs to see and test every endpoint
in a browser — FastAPI generates that automatically.
"""

import os
import json
import psycopg2
from datetime import date as date_cls
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from build_paper import build_todays_paper, get_past_edition, get_available_editions, get_user_profile

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    the sources + topics + reading-time screens actually call now.
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
