#!/bin/bash
# Runs the full Throughline pipeline in order: ingest -> embed -> cluster
# -> synthesize -> build today's papers -> generate context.
#
# In production this runs on a schedule via .github/workflows/daily-warm.yml
# (GitHub Actions, once a day, straight against the database — free, no
# host-side cron job needed). build_paper.py's ensure_todays_data_is_fresh()
# also triggers the same refresh lazily as a fallback, the first time
# anyone's edition is built on a day this hasn't run yet. This script itself
# is also handy for manually forcing a full run locally (testing, debugging).
#
# build_paper runs before generate_context on purpose: generate_context
# only fills in history/timeline/AI-summary for stories that actually got
# selected into someone's edition today (see its paper_editions join), so
# the editions have to exist first.
#
# Run manually with: ./run_pipeline.sh

# Always run relative to wherever THIS script lives, not wherever it was
# launched from (cron/CI launch scripts from a blank environment, so this
# matters — without it, the python scripts would fail to find .env etc).
cd "$(dirname "$0")"

# Plain `python3` from PATH — portable across local dev and CI, unlike a
# hardcoded interpreter path that only exists on one machine.
PYTHON="python3"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/pipeline_$(date +%Y-%m-%d_%H-%M-%S).log"

echo "=== Throughline pipeline run: $(date) ===" | tee -a "$LOG_FILE"

echo "--- Ingesting articles ---" | tee -a "$LOG_FILE"
"$PYTHON" ingest.py >> "$LOG_FILE" 2>&1

echo "--- Embedding new articles ---" | tee -a "$LOG_FILE"
"$PYTHON" embed_all.py >> "$LOG_FILE" 2>&1

echo "--- Clustering ---" | tee -a "$LOG_FILE"
"$PYTHON" cluster.py >> "$LOG_FILE" 2>&1

echo "--- Synthesizing stories ---" | tee -a "$LOG_FILE"
"$PYTHON" synthesize_all.py >> "$LOG_FILE" 2>&1

echo "--- Building today's papers (all users) ---" | tee -a "$LOG_FILE"
"$PYTHON" build_paper.py >> "$LOG_FILE" 2>&1

echo "--- Generating context (history / catch-up / AI summary) ---" | tee -a "$LOG_FILE"
"$PYTHON" generate_context.py >> "$LOG_FILE" 2>&1

echo "=== Done: $(date) ===" | tee -a "$LOG_FILE"
