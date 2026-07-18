-- This turns on pgvector, the add-on that lets Postgres store and compare
-- embeddings later. We don't need it yet for ingestion, but it's free to
-- enable now so we don't have to think about it again.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS articles (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    body            TEXT,              -- full article text if the feed gives it, else the summary
    source          TEXT NOT NULL,     -- 'BBC', 'Al Jazeera', 'The Guardian', 'NPR', 'AP'
    url             TEXT NOT NULL UNIQUE,  -- UNIQUE is what stops us saving the same article twice
    published_at    TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ DEFAULT now(),
    embedding       vector(384)        -- 384 numbers, matching the local embedding model we're using
);

-- Speeds up "show me articles from the last 48 hours" queries, which
-- we'll be running constantly once clustering is running.
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles (published_at DESC);

-- A story is a group of articles about the same event. centroid_embedding
-- is the average of every article assigned to it — think of it as "the
-- coordinates of what this story is about," which is what new articles
-- get compared against to decide if they belong here.
CREATE TABLE IF NOT EXISTS stories (
    id                  SERIAL PRIMARY KEY,
    centroid_embedding  vector(384),
    headline            TEXT,
    summary             TEXT,
    key_facts           TEXT[],
    disputed_facts      TEXT[],
    importance_score    INTEGER,
    topics              TEXT[],
    entities            TEXT[],
    historical_context  JSONB,  -- deep background timeline, or [] if not applicable to this story
    recent_timeline     JSONB,  -- what's happened in the last few days, chronologically
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE articles ADD COLUMN IF NOT EXISTS story_id INTEGER REFERENCES stories(id);

-- Tracks which stories have already been shown, and when — this is what
-- lets us tell "same old story, nothing new" apart from "genuine update"
-- apart from "brand new event."
CREATE TABLE IF NOT EXISTS paper_editions (
    id              SERIAL PRIMARY KEY,
    edition_date    DATE NOT NULL,
    story_id        INTEGER REFERENCES stories(id),
    status          TEXT,   -- 'new' or 'update'
    catch_up_text   TEXT,   -- only filled in for 'update' stories
    shown_at        TIMESTAMPTZ DEFAULT now()
);
