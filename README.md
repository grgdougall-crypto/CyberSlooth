# CyberSlooth

CyberSlooth is a planned public experiment in internet archaeology: autonomous research, daily discovery, and forgotten corners of the web.

## Prototype status

Stage 0.2 preserves the simulated daily expedition from Stage 0.1 and adds one bounded public-URL evidence retrieval workflow. A Flask backend validates and retrieves one public HTML or plain-text page, extracts visible evidence and candidate links, and returns explicit source provenance. It does not use AI, follow candidate links, crawl recursively, schedule work, or persist records.

## Run locally

From `C:\Project CyberSlooth`:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open `http://localhost:8000`.

## Evidence contract

`POST /api/ingest` accepts `{ "url": "https://example.com/page" }` and returns a normalized evidence object with three boundaries: immutable source metadata, extracted page content, and unvisited candidate links. The separate `analysis` marker is always false in Stage 0.2, leaving a small, documented seam for Stage 0.3 without implying that analysis exists today.

## Railway readiness

`Procfile` runs `gunicorn app:app`. The application binds to `0.0.0.0` and reads Railway's `PORT` environment variable when started directly; no Railway resources or deployment configuration have been created.

## Likely future stages

- Stage 0.3 — AI-assisted analysis of one normalized evidence record
- Stage 0.4 — bounded candidate-link exploration
- Stage 0.5 — persistent research archive
- Stage 1.0 — scheduled autonomous expedition

These future stages are not implemented here.
