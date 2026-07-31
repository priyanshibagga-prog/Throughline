# Throughline
A personalized daily newspaper. Throughline pulls in articles from trusted news outlets, clusters them into stories, uses an LLM to synthesize and rank them, and builds each reader a personalised daily edition.

## Why this exists

| The problem | What Throughline does |
|---|---|
| So many sources, so much information — don't know which to pick, reading news feels overwhelming | You select which sources you trust; every story draws from all of them, not one at a time |
| Don't know what to read, endless never-ending articles | Personalizes by recency, importance, and relevance to you — a fixed number of articles per edition, not an infinite feed |
| Feel like you're always playing catch-up | A "catch up" timeline shows recent events on any ongoing story, so you're never lost mid-conflict |
| No clue who or what the historical context is for ongoing conflicts | An expandable historical context timeline gives the background — only when a story actually needs it |

## How it works

The pipeline runs in stages, each one a standalone script that reads from and writes to a shared Postgres database:

```
ingest.py          → pulls the latest articles from each source's RSS feed
embed_all.py        → embeds each article (sentence-transformers, all-MiniLM-L6-v2)
cluster.py           → groups articles covering the same event into "stories"
synthesize_all.py    → LLM pass: headline, summary, topics, entities, importance score
generate_context.py  → LLM pass: AI summary, historical context, recent-events timeline
build_paper.py        → selects and ranks stories into each user's personalized edition
```

`run_pipeline.sh` runs all of them in order and is meant to be scheduled (cron, etc.) every 15–30 minutes.

### API

FastAPI app in `api.py`:

| Endpoint | Description |
|---|---|
| `GET /api/paper/{user_id}` | Today's edition (or `?edition_date=YYYY-MM-DD` to reconstruct a past one, read-only) |
| `GET /api/editions/{user_id}` | Every date this user has an edition for |
| `GET /api/sources` / `GET /api/topics` | Fixed onboarding option lists |
| `POST /api/users` | Create or update a user's profile (upserts by email) |

### Frontend

React + Vite + Tailwind, in `frontend/`. Onboarding: sign in → pick sources → pick topics and star-rate how much each one matters → pick reading time → land on your edition. From the account menu (click the avatar, top right) you can change any of these later, or log out — preference changes apply starting your *next* edition, not retroactively to today's.

Each story shows a headline, a short deck, a static "AI Summary" box (a longer, LLM-generated summary of the full article — distinct from the deck), a link to the original, and — when available — expandable "Historical context" and "Catch up" (recent timeline) sections.

## Setup

1. **Database**: Postgres with the `pgvector` extension. Run `db/schema.sql` against it.
2. **Environment**: copy `.env.example` to `.env` and fill in `DATABASE_URL` and `GROQ_API_KEY` (used for the LLM synthesis/context steps).
3. **Backend**:
   ```bash
   pip install -r requirements.txt
   python3 create_test_user.py      # or sign up through the frontend
   ./run_pipeline.sh                 # or run each stage individually
   uvicorn api:app --reload
   ```
4. **Frontend**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

## Notes

- Full article text is fetched on demand (via `trafilatura`) the first time a story is shown, then cached — not during ingestion, to avoid scraping articles nobody ends up reading.
- `view_story.py` and `clean_existing_data.py` are small debugging/maintenance utilities, not part of the scheduled pipeline.
