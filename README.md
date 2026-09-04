# CyberSlooth

CyberSlooth is a planned public experiment in internet archaeology: autonomous research, daily discovery, and forgotten corners of the web.

## Prototype status

Stage 0.4 preserves the simulated expedition, bounded public-URL retrieval, and evidence-only analysis, then adds one bounded exploration step. From one validated Stage 0.3 research record, the model may rank at most five original candidates and select no more than two. Application code retrieves those pages sequentially with the existing safety controls, analyzes each successful page separately, produces one comparison, and stops.

Stage 0.4 is one-hop only. Links discovered in follow-up pages may be shown as a suggested future lead, but they are never fetched by the same request. There is no recursive crawling, persistence, scheduling, background work, or automatic publishing.

## Run locally

From `C:\Project CyberSlooth`:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open `http://localhost:8000`.

## Evidence, analysis, and exploration pipeline

Deterministic retrieval → structured AI analysis → candidate ranking → max 2 safe follow-up fetches → bounded follow-up analysis → synthesis → **STOP**.

`POST /api/ingest` accepts `{ "url": "https://example.com/page" }` and returns a normalized evidence object with immutable source metadata, extracted page content, and unvisited candidate links. `POST /api/analyze` accepts exactly that normalized object—not a raw prompt—and returns a compact analysis containing grounded observations, explicit uncertainties, optional candidate follow-ups, an archive recommendation, and confidence.

Candidate links are never presented to the model as visited evidence. Any follow-up URL in the model output must exactly match a candidate from the supplied record, and the application rejects invented URLs.

`POST /api/explore` accepts exactly `{ "evidence": normalizedEvidence, "analysis": validatedAnalysis }`; it does not accept a free-form prompt. The endpoint enforces a maximum of five original candidates, two selected pages, one hop, and four model calls: one selector, up to two follow-up analyses, and one synthesis. Every model call is tool-free and uses strict structured output. Follow-up retrieval reuses the same URL, DNS, private-address, redirect, timeout, response-size, content-type, and provenance controls as initial ingestion.

Original and follow-up evidence remain separate. A failed follow-up is returned as an explicit failed expedition item without erasing the original or another successful follow-up. If every selected follow-up fails, the endpoint returns a bounded failure result and stops without retrying or substituting another link.

## Railway readiness

`Procfile` runs `gunicorn app:app`. The application binds to `0.0.0.0` and reads Railway's `PORT` environment variable when started directly.

Railway must provide:

- `OPENAI_API_KEY` — required for analysis
- `OPENAI_MODEL` — optional; defaults to `gpt-5.4-mini`

No Railway variables or resources are created by this project.

## Likely future stages

- Stage 0.5 — persistent research archive
- Stage 1.0 — scheduled autonomous expedition

These future stages are not implemented here.
