# CyberSlooth

CyberSlooth is a planned public experiment in internet archaeology: autonomous research, daily discovery, and forgotten corners of the web.

## Prototype status

Stage 1.0B prepares the tested autonomous expedition for one external Railway-scheduled invocation per day. The same orchestrator rotates through an application-owned seed pool, reuses the existing bounded research services, archives the result, evaluates recent discoveries, publishes one selected archive record, records sanitized run metadata, and stops.

Stage 1.0B contains no application-level scheduler. Railway is the clock: each scheduled process invokes the existing CLI once, performs at most one expedition, and exits. The CLI and protected HTTP manual triggers remain available. CyberSlooth adds no cron library, background worker, queue, polling loop, recursive crawl, multi-hop research, or repeated expedition loop.

## Run locally

From `C:\Project CyberSlooth`:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open `http://localhost:8000`.

## Research and archive pipeline

Railway daily schedule → existing tested autonomous orchestrator → bounded research expedition → persistent archive → daily discovery publication → **STOP**.

Within the bounded expedition: curated seed rotation → safe retrieval → structured AI analysis → bounded one-hop exploration → synthesis → automatic archive → recent-discovery scoring → daily discovery publication → **STOP**.

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

Stage 1.0A created the `autonomous_runs` and `daily_discoveries` tables at startup while retaining the bounded additive Stage 0.6 upgrade for older `research_runs` tables. Formal versioned migrations are not yet included.

## Railway readiness

`Procfile` runs `gunicorn app:app`. The application binds to `0.0.0.0` and reads Railway's `PORT` environment variable when started directly.

Railway must provide:

- `OPENAI_API_KEY` — required for analysis
- `OPENAI_MODEL` — optional; defaults to `gpt-5.4-mini`
- `DATABASE_URL` — required for persistent Railway Postgres storage
- `AUTONOMY_RUN_TOKEN` — required for the protected HTTP trigger; use a long, random secret

### Railway scheduled service setup

Create a separate scheduled/cron service in Railway from the **same CyberSlooth GitHub repository**. Do not create a second implementation or add a web server start command to that service.

Set its scheduled command to:

```text
python autonomous_run.py
```

Give the scheduled service access to the same production values used by the web service:

- `DATABASE_URL` — the connection URL for the **same Railway Postgres database** used by the main CyberSlooth web service
- `OPENAI_API_KEY` — the model-provider credential
- `OPENAI_MODEL` — the same explicitly selected model used by the web service

The scheduled CLI does not require `AUTONOMY_RUN_TOKEN`; that token remains required by the web service's protected HTTP trigger. When Railway is detected without `DATABASE_URL`, startup fails closed instead of using ephemeral SQLite.

Optionally set these informational variables on the web service so `/status` reflects the Railway configuration:

- `AUTONOMY_SCHEDULE_ENABLED=true`
- `AUTONOMY_SCHEDULE_CRON=<the actual Railway cron expression>`

These values never create, parse, or execute a schedule. Configure the desired daily UTC schedule in Railway itself. One Railway process invocation runs at most one expedition and then exits.

No Railway variables, Postgres services, or other resources are created by this project.

Candidate selection uses at most one model call per manual request, considers at most ten records, uses no tools, and relies on the OpenAI client's zero-retry configuration. Stored page-derived text is explicitly treated as untrusted data in the scoring prompt. Raw excerpts, URLs, provider responses, secrets, internal database IDs, and hidden prompts are excluded from the scoring payload and selection metadata.

## Stage 1.0B scheduled execution

`run_autonomous_expedition()` remains the single orchestration path used by the Railway CLI invocation and both manual triggers. It calls the existing Python services directly and never makes HTTP requests back into CyberSlooth's own API.

Each successful run:

1. Creates an `AutonomousRun` in `running` state and acquires the database-backed active-run guard.
2. Selects one primary enabled seed deterministically using least-recently-used order with stable seed-ID tie-breaking.
3. Retrieves the primary starting page using the existing safety controls. If that source has a retryable retrieval failure, it tries at most one different enabled seed from the same curated pool, then analyzes the first successfully retrieved starting page.
4. Uses the existing one-hop exploration when the validated analysis contains candidate follow-ups; at most two pages may be selected.
5. Validates and archives the completed research structure.
6. Scores only the ten most recent archive records using the existing Stage 0.6 one-call evaluator.
7. Publishes one `DailyDiscovery` that references the selected archived record.
8. Completes the run record and stops without starting another expedition.

The starter seed pool is stored in `data/autonomy_seeds.json`. It contains five benign public archive or informational starting points and can be curated without changing orchestration code. Primary and alternate seed selection use no model call. The alternate must be a different enabled seed from this approved pool: arbitrary trigger-supplied or fallback URLs are never accepted.

An autonomous run may use at most six model calls: one starting analysis, up to four existing exploration calls, and one cross-run scoring call. Starting-source retrieval is limited to one primary curated seed plus at most one alternate curated seed, for a maximum of two starting-seed retrieval attempts. After one starting page succeeds, retrieval remains limited to at most two follow-up pages. Existing clients use zero automatic retries.

Failures are recorded with a sanitized stage and message. Retrieval or analysis failure creates no archive or publication. Archive failure does not publish. Scoring failure preserves the newly archived record and the prior publication. Publication failure preserves the archive, ranking metadata, and prior publication. A unique active-run guard blocks concurrent execution, and a completed run blocks another normal run during the same UTC date. Failed runs may be manually retried.

## CLI and manual invocation

CLI execution is both the Railway scheduled entrypoint and a manual operational trigger. It runs directly in the application environment, performs at most one expedition, emits concise sanitized status, and does not require the HTTP trigger token:

```powershell
python autonomous_run.py
```

The internal HTTP trigger requires `AUTONOMY_RUN_TOKEN` and an exact bearer token. It accepts no run configuration or starting URL:

```powershell
$headers = @{ Authorization = "Bearer $env:AUTONOMY_RUN_TOKEN" }
Invoke-RestMethod -Method Post -Uri "https://YOUR-SERVICE/api/autonomous-run" -Headers $headers
```

The token is never included in CLI output, frontend JavaScript, API responses, public status, or application logs.

## Public daily discovery and status

- `/today` displays the current published daily discovery, selection reason and score, archive metadata, exploration state, full-record link, and explicit run boundaries. It provides an archive-linked empty state before the first publication.
- `/status` displays only sanitized metadata for the latest autonomous run plus informational Railway schedule mode: public run ID, timestamps, status, page and model-call counts, publication state, safe failure stage, and applicable archive links. It does not calculate a next run or control scheduling.
- `/archive` and archive detail pages distinguish a published `DAILY DISCOVERY` from an evaluated `DAILY CANDIDATE`.

`AutonomousRun` stores the public run ID, timestamps, status, initial seed ID, final seed reference, seed-attempt count, archive/publication references, bounded counters, and safe failure metadata. Seed URLs remain excluded from public status. `DailyDiscovery` stores one unique publication per UTC date and references the full `ResearchRun` rather than duplicating it.

Railway configuration is intentionally not created by this repository. Stage 1.0B only makes the existing short-lived execution path ready for Railway's external scheduler.
