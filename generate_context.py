"""
Generates three things per story, matching the mockup's expand buttons:

1. AI summary — a longer, more substantive summary of the full
   representative article, grounded entirely in that article's own text.
   Distinct from stories.summary (the punchy 2-3 sentence teaser used as
   the deck/subhead everywhere) — this is the "explain the whole article
   to me" version shown behind the "AI Summary" expand button.

2. Historical context — deep background explaining WHY this is happening.
   Only generated if the story genuinely needs it (skipped for sports
   scores, routine announcements, etc). Uses the LLM's general knowledge,
   not this story's articles — so treat it as a helpful primer, not a
   guaranteed-accurate source; a real version of this would ground it in
   something like Wikipedia rather than the model's memory alone.

3. Recent timeline — what's happened in the last few days on THIS story,
   built entirely from its own articles, grouped by day.
"""

import os
import json
import time
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.environ["GROQ_API_KEY"])
conn = psycopg2.connect(os.environ["DATABASE_URL"])



def call_llm(prompt):
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def generate_ai_summary(headline, summary, articles):
    """
    Summarizes the single longest (most complete) article in the cluster —
    the same one the frontend links to as "Read full story" — rather than
    re-stating the short cross-source synthesis summary. Grounded only in
    that article's own text, no outside knowledge.
    """
    longest = max(articles, key=lambda a: len(a[2] or ""), default=None)
    if not longest or not (longest[2] or "").strip():
        return None
    title, source, body = longest

    prompt = f"""Story: "{headline}"

A short teaser summary already shown to the reader elsewhere: "{summary}"

Full article ({source}): {title}
{body[:6000]}

Write a longer, more substantive AI summary of this article — 4-6 sentences.
It should let someone who reads ONLY this skip the full article and still
understand what happened, the key facts and figures, relevant context, and
what's likely to happen next, if the article says. Do not just reword the
short teaser summary above — add the specifics and detail it leaves out.
Base this only on the article text given, not outside knowledge.

Respond with ONLY this JSON shape:
{{"ai_summary": "..."}}"""
    result = call_llm(prompt)
    return result.get("ai_summary") or None


def generate_historical_context(headline, summary, articles):
    # Outlets often include their own recap of earlier events inside the
    # article body (especially live-blog style pieces). Pull the longest
    # few articles as likely candidates for containing that recap — this
    # is real journalism grounding it, not the model guessing from memory.
    longest_articles = sorted(articles, key=lambda a: len(a[2] or ""), reverse=True)[:3]
    source_excerpts = "\n\n---\n\n".join(
        f"[{source}] {title}\n{(body or '')[:1500]}"
        for title, source, body in longest_articles
    )

    prompt = f"""Story: "{headline}"
Summary: {summary}

Some source articles below (they may already contain their own recap of earlier related events — use any such recap as real, grounded information):

{source_excerpts}

Does understanding this story benefit from historical background — e.g. a long-running conflict, a political history, a deep-rooted dispute? Routine news (sports results, single crimes, one-off announcements) usually does NOT need this.

If NO meaningful history applies, respond with exactly: {{"needed": false}}

If YES, respond with ONLY this JSON shape:
{{
  "needed": true,
  "timeline": [
    {{"date": "1953", "text": "short description of a key historical event"}}
  ]
}}
Include 5-8 entries. Prioritize: (1) any earlier events the articles themselves
recap, then (2) well-established, widely-documented historical milestones you're
confident about. Write each entry so it connects to the next (cause -> effect),
not as disconnected facts. Do NOT include an entry for the current/recent
situation in the headline — that's covered separately, from real recent articles."""
    result = call_llm(prompt)
    if not result.get("needed"):
        return []
    return result.get("timeline", [])


def day_label(dt, today):
    days_ago = (today - dt.date()).days
    if days_ago == 0:
        return "Today"
    if days_ago == 1:
        return "Yesterday"
    if days_ago < 7:
        return dt.strftime("%A")  # e.g. "Monday"
    return dt.strftime("%b %d")


def generate_recent_timeline(headline, articles):
    """
    Uses the FULL history of this story's own articles — from whenever it
    actually started, not an arbitrary fixed window — so someone catching
    up gets the real beginning, not just "the last 5 days" cut off
    mid-story. Written as a natural catch-up, like explaining it to a
    friend who asks "what's going on with this?", not a dry bullet log.
    """
    today = datetime.now(timezone.utc).date()

    by_day = {}
    for title, source, body, published_at in articles:
        label = day_label(published_at, today)
        by_day.setdefault((published_at.date(), label), []).append((title, source, body))

    if len(by_day) < 2:
        return []  # only one day of coverage so far — nothing to "catch up" on yet

    sorted_days = sorted(by_day.items(), key=lambda x: x[0][0])
    blocks = []
    for (day_date, label), day_articles in sorted_days:
        articles_text = "\n\n".join(
            f"[{source}] {title}\n{(body or '')[:1200]}"
            for title, source, body in day_articles
        )
        blocks.append(f"DATE: {label}\n{articles_text}")

    prompt = f"""Story: "{headline}"

Full coverage of this story so far, grouped by day, starting from when it began:

{chr(10).join(('---' + chr(10) + b) for b in blocks)}

Some of these articles may themselves contain a recap of EARLIER related
events (before the dates listed above) — if so, pull that out as real,
grounded information and include it as its own earlier entry/entries in
the timeline, even though we don't have separately dated articles for it.

Imagine a friend just asked you "hey, what's going on with this?" — write a
natural, easy-to-follow catch-up covering how it's unfolded, starting from
the actual beginning (including any earlier events the articles recap).
Conversational, not a dry headline list.

Respond with ONLY this JSON shape, ordered chronologically:
{{
  "timeline": [
    {{"date": "<a date label — either one of the ones given, or your best label for an earlier recapped event, e.g. 'Earlier' or a month/year>", "text": "1-2 natural sentences, as if explaining it to a friend"}}
  ]
}}"""
    result = call_llm(prompt)
    return result.get("timeline", [])


def main():
    import sys
    if len(sys.argv) > 1:
        # Single-story mode: python3 generate_context.py <story_id>
        # Regenerates just this one story, without touching anything else —
        # use this for testing instead of resetting the whole table.
        story_id = int(sys.argv[1])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, headline, summary, NULL, NULL FROM stories WHERE id = %s;",
                (story_id,),
            )
            stories = cur.fetchall()
    else:
        with conn.cursor() as cur:
            # Only stories that were actually SELECTED into someone's
            # edition today — not the whole backlog of synthesized
            # stories. Most synthesized stories never get shown to
            # anyone; generating history/timeline/AI-summary for all of
            # them burns through the LLM's rate limit on content nobody
            # will ever read. build_paper.py has to run (for every user)
            # before this, since that's what populates paper_editions.
            # Freshest first within that set, in case the rate limit
            # still cuts a run short.
            cur.execute("""
                SELECT s.id, s.headline, s.summary, s.historical_context, s.ai_summary
                FROM stories s
                JOIN articles a ON a.story_id = s.id
                WHERE s.headline IS NOT NULL
                  AND (s.historical_context IS NULL OR s.ai_summary IS NULL)
                  AND EXISTS (
                      SELECT 1 FROM paper_editions pe
                      WHERE pe.story_id = s.id AND pe.edition_date = CURRENT_DATE
                  )
                GROUP BY s.id, s.headline, s.summary, s.historical_context, s.ai_summary
                ORDER BY MAX(a.published_at) DESC;
            """)
            stories = cur.fetchall()

    print(f"Generating context for {len(stories)} stories...\n")

    for story_id, headline, summary, existing_historical, existing_ai_summary in stories:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT title, source, body, published_at FROM articles WHERE story_id = %s;", (story_id,))
                articles_full = cur.fetchall()
            # historical context and ai summary want (title, source, body); recent timeline wants published_at too
            articles_no_date = [(t, s, b) for t, s, b, _ in articles_full]

            ai_summary = existing_ai_summary
            if ai_summary is None:
                ai_summary = generate_ai_summary(headline, summary, articles_no_date)
                time.sleep(1.2)

            historical = existing_historical
            if historical is None:
                historical = generate_historical_context(headline, summary, articles_no_date)
                time.sleep(1.2)

            recent = generate_recent_timeline(headline, articles_full)
            time.sleep(1.2)

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE stories SET ai_summary = %s, historical_context = %s, recent_timeline = %s WHERE id = %s;",
                    (ai_summary, psycopg2.extras.Json(historical), psycopg2.extras.Json(recent), story_id),
                )
            conn.commit()

            print(f"  #{story_id} — {headline}")
            print(f"      ai_summary: {'generated' if ai_summary else 'none'}  |  history: {len(historical or [])} entries  |  recent: {len(recent)} entries")

        except Exception as e:
            print(f"  #{story_id} FAILED: {e}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
