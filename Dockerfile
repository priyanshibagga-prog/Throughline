# Backend image for Koyeb (or any other Docker-friendly host). Only
# runs the API — the daily ingest/embed/cluster/synthesize refresh
# happens in GitHub Actions instead (see .github/workflows), directly
# against the database, so this image doesn't need to be big or fast
# to cold-start for that part.
FROM python:3.11-slim

WORKDIR /app

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY . .

# Koyeb (like most hosts) sets $PORT at runtime; default to 8000 for
# local `docker run` testing where it isn't set.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
