# CyberSlooth

CyberSlooth is a planned public experiment in internet archaeology: autonomous research, daily discovery, and forgotten corners of the web.

## Prototype status

Stage 0.6 preserves the complete bounded research and archive pipeline, then adds an explicit comparison of recent archived discoveries. A user can evaluate up to the ten newest records and mark exactly one preferred daily candidate using validated component scores and deterministic server-side ranking.

Archival and candidate evaluation are manual. Stage 0.6 does **not** schedule runs, choose seeds, publish automatically, run background jobs, crawl recursively, or perform multi-hop research.

## Run locally

From `C:\Project CyberSlooth`:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open `http://localhost:8000`.

## Research and archive pipeline

Safe retrieval → structured AI analysis → bounded one-hop exploration → synthesis → persistent archive → bounded cross-run scoring → manually selected daily candidate.

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

## Daily candidate selection

`POST /api/select-daily-candidate` accepts no browser-supplied records. The server loads only the ten most recent `ResearchRun` rows and requires at least two. It sends one compact, stored-data-only comparison to the OpenAI Responses API with tools disabled and strict structured output.

Each record receives integer 0–5 scores for research value, evidence quality, novelty, interestingness, uncertainty penalty, and archive recommendation quality. The server ignores the model's arithmetic and recomputes:

`research value + evidence quality + novelty + interestingness + archive quality - uncertainty penalty`

The possible total is -5 through 25. Ties are resolved by higher evidence quality, then higher research value, then newer archive order. IDs must exactly match the supplied public IDs, and every supplied record must be scored exactly once.

Successful evaluation atomically stores each candidate's total, rank, evaluation time, and selected flag while clearing the prior selected flag. Provider, validation, or database failures leave the previous completed selection unchanged. The `/archive` page exposes the manual **Evaluate Recent Discoveries** action, winner, and ranked result; a selected record receives a `DAILY CANDIDATE` badge. This is selection metadata, not a publication record.

## Database configuration

CyberSlooth uses SQLAlchemy through one persistence layer.

Local development defaults to SQLite at `data/cyberslooth.db` when `DATABASE_URL` and Railway environment markers are absent. The database file and SQLite sidecar files are excluded from Git.

Production on Railway requires a Postgres service and its `DATABASE_URL` variable. Common Railway `postgres://` and `postgresql://` URLs are normalized to SQLAlchemy's Psycopg driver form. CyberSlooth deliberately refuses to fall back to ephemeral SQLite when a Railway environment is detected without `DATABASE_URL`.

Stage 0.6 creates the table for a fresh installation and applies a bounded additive startup upgrade for the four nullable candidate-metadata columns on an existing Stage 0.5 database. Formal versioned migrations are not yet included.

## Railway readiness

`Procfile` runs `gunicorn app:app`. The application binds to `0.0.0.0` and reads Railway's `PORT` environment variable when started directly.

Railway must provide:

- `OPENAI_API_KEY` — required for analysis
- `OPENAI_MODEL` — optional; defaults to `gpt-5.4-mini`
- `DATABASE_URL` — required for persistent Railway Postgres storage

No Railway variables, Postgres services, or other resources are created by this project.

Candidate selection uses at most one model call per manual request, considers at most ten records, uses no tools, and relies on the OpenAI client's zero-retry configuration. Stored page-derived text is explicitly treated as untrusted data in the scoring prompt. Raw excerpts, URLs, provider responses, secrets, internal database IDs, and hidden prompts are excluded from the scoring payload and selection metadata.

## Likely future stages

- Stage 1.0 — scheduled autonomous expedition

Scheduling and autonomous publishing are not implemented here.
