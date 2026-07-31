#!/bin/bash
# Runs the full Throughline pipeline in order: ingest -> embed -> cluster
# -> synthesize -> build today's papers -> generate context.
#
# build_paper runs before generate_context on purpose: generate_context
# only fills in history/timeline/AI-summary for stories that actually got
# selected into someone's edition today (see its paper_editions join), so
# the editions have to exist first.
#
# Run manually with: ./run_pipeline.sh
# Or scheduled to run automatically — see the cron instructions.

# Always run relative to wherever THIS script lives, not wherever it was
# launched from (cron launches scripts from a blank environment, so this
# matters — without it, the python scripts would fail to find .env etc).
cd "$(dirname "$0")"

PYTHON="/Library/Frameworks/Python.framework/Versions/3.10/bin/python3"

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
