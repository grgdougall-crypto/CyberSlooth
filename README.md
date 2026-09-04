# CyberSlooth

CyberSlooth is a planned public experiment in internet archaeology: autonomous research, daily discovery, and forgotten corners of the web.

## Prototype status

Stage 0.5 preserves the complete bounded Stage 0.4 pipeline and adds explicit, durable archival. A user may save either a validated analyzed-only record or a successfully synthesized one-hop exploration. Stored records remain provenance-separated and can be browsed through the public archive index and read-only detail pages.

Archival is never automatic. Stage 0.5 still has no recursive crawling, scheduling, background work, discovery ranking across runs, or automatic daily publishing.

## Run locally

From `C:\Project CyberSlooth`:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open `http://localhost:8000`.

## Research and archive pipeline

Safe retrieval → structured AI analysis → bounded one-hop exploration → synthesis → explicit archive action → persistent public record.

`POST /api/ingest` accepts `{ "url": "https://example.com/page" }` and returns a normalized evidence object with immutable source metadata, extracted page content, and unvisited candidate links. `POST /api/analyze` accepts exactly that normalized object—not a raw prompt—and returns a compact analysis containing grounded observations, explicit uncertainties, optional candidate follow-ups, an archive recommendation, and confidence.

Candidate links are never presented to the model as visited evidence. Any follow-up URL in the model output must exactly match a candidate from the supplied record, and the application rejects invented URLs.

`POST /api/explore` accepts exactly `{ "evidence": normalizedEvidence, "analysis": validatedAnalysis }`; it does not accept a free-form prompt. The endpoint enforces a maximum of five original candidates, two selected pages, one hop, and four model calls: one selector, up to two follow-up analyses, and one synthesis. Every model call is tool-free and uses strict structured output. Follow-up retrieval reuses the same URL, DNS, private-address, redirect, timeout, response-size, content-type, and provenance controls as initial ingestion.

Original and follow-up evidence remain separate. A failed follow-up is returned as an explicit failed expedition item without erasing the original or another successful follow-up. If every selected follow-up fails, the endpoint returns a bounded failure result and stops without retrying or substituting another link.

## Persistent archive

`POST /api/archive` accepts only validated CyberSlooth evidence, analysis, and an optional completed exploration. The server re-validates all structures, list limits, enum values, URL provenance, stop markers, model-call counts, and synthesis before storing anything. It generates a non-sequential public ID and returns a relative `/archive/<public_id>` URL.

The endpoint stores normalized excerpts and structured results only—never API keys, provider objects, hidden prompts, raw provider responses, or arbitrary browser metadata. Identical payloads submitted within ten minutes return the existing public record instead of creating an accidental duplicate.

- `/archive` lists persisted records newest first.
- `/archive/<public_id>` displays original evidence, AI analysis, visited follow-ups, synthesis, uncertainties, provenance, and archive metadata.
- Analysis-only records store `exploration_performed = false` with no exploration or synthesis JSON.

## Database configuration

CyberSlooth uses SQLAlchemy through one persistence layer.

Local development defaults to SQLite at `data/cyberslooth.db` when `DATABASE_URL` and Railway environment markers are absent. The database file and SQLite sidecar files are excluded from Git.

Production on Railway requires a Postgres service and its `DATABASE_URL` variable. Common Railway `postgres://` and `postgresql://` URLs are normalized to SQLAlchemy's Psycopg driver form. CyberSlooth deliberately refuses to fall back to ephemeral SQLite when a Railway environment is detected without `DATABASE_URL`.

Stage 0.5 automatically creates the initial table at application startup. This keeps the prototype simple; formal migrations are not yet included.

## Railway readiness

`Procfile` runs `gunicorn app:app`. The application binds to `0.0.0.0` and reads Railway's `PORT` environment variable when started directly.

Railway must provide:

- `OPENAI_API_KEY` — required for analysis
- `OPENAI_MODEL` — optional; defaults to `gpt-5.4-mini`
- `DATABASE_URL` — required for persistent Railway Postgres storage

No Railway variables, Postgres services, or other resources are created by this project.

## Likely future stages

- Stage 1.0 — scheduled autonomous expedition

Scheduling and autonomous publishing are not implemented here.
