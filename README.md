# CyberSlooth

CyberSlooth is a planned public experiment in internet archaeology: autonomous research, daily discovery, and forgotten corners of the web.

## Prototype status

Stage 0.3 preserves the simulated expedition and bounded public-URL retrieval, then adds optional AI-assisted analysis of one normalized evidence record. The model is limited to supplied evidence, receives no web tools, and returns a strict structured result that the application validates before display. CyberSlooth still does not follow candidate links, crawl recursively, schedule work, or persist records.

## Run locally

From `C:\Project CyberSlooth`:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open `http://localhost:8000`.

## Evidence and analysis pipeline

Deterministic retrieval → normalized evidence → AI-assisted structured analysis → application validation → display.

`POST /api/ingest` accepts `{ "url": "https://example.com/page" }` and returns a normalized evidence object with immutable source metadata, extracted page content, and unvisited candidate links. `POST /api/analyze` accepts exactly that normalized object—not a raw prompt—and returns a compact analysis containing grounded observations, explicit uncertainties, optional candidate follow-ups, an archive recommendation, and confidence.

Candidate links are never presented to the model as visited evidence. Any follow-up URL in the model output must exactly match a candidate from the supplied record, and the application rejects invented URLs.

## Railway readiness

`Procfile` runs `gunicorn app:app`. The application binds to `0.0.0.0` and reads Railway's `PORT` environment variable when started directly.

Railway must provide:

- `OPENAI_API_KEY` — required for analysis
- `OPENAI_MODEL` — optional; defaults to `gpt-5.4-mini`

No Railway variables or resources are created by this project.

## Likely future stages

- Stage 0.4 — bounded candidate-link exploration
- Stage 0.5 — persistent research archive
- Stage 1.0 — scheduled autonomous expedition

These future stages are not implemented here.
