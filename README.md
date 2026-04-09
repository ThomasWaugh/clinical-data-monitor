# Clinical Data Monitor

A real-time patient vitals monitoring system with three-layer anomaly detection and AI-generated clinical explanations. Synthetic vital signs stream live to a dashboard; three detector algorithms flag drifts and anomalies as they occur; Claude generates a plain-English clinical explanation for each event.

**[Live demo →](https://clinical-data-monitor.onrender.com)**

![Python](https://img.shields.io/badge/Python-3.12-3776ab?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Anthropic](https://img.shields.io/badge/Claude_API-Anthropic-d4a574)
![Evidently](https://img.shields.io/badge/Evidently-ML_monitoring-blue)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Deployed on Render](https://img.shields.io/badge/Deployed_on-Render-46e3b7?logo=render&logoColor=white)

---

## What it does

ICU monitoring systems generate continuous streams of vital sign data. Detecting clinically meaningful changes — gradual drifts, acute spikes, distributional shifts — across five vitals simultaneously is a signal processing problem as much as a clinical one.

This system accepts a continuous stream of synthetic patient vitals (Heart Rate, SpO₂, Systolic BP, Respiratory Rate, Temperature) and:

- **Detects gradual drift** using CUSUM (Cumulative Sum Control), which accumulates small persistent deviations to flag changes that no single reading would trigger
- **Detects acute anomalies** using a rolling Z-score that flags readings more than 3 SD from the recent rolling mean
- **Detects distributional shift** using Evidently's DataDriftPreset, comparing batched current readings against a reference window to identify population-level changes
- **Explains each event** by calling Claude, which returns a structured clinical interpretation — headline, explanation, suggested action, and severity — written for a clinician, not a data scientist
- **Streams everything live** to a dark-mode dashboard via Server-Sent Events, with real-time charts for all five vitals and a timestamped event feed with explanations

---

## Who would use this

- **ICU nurses** monitoring multiple patients simultaneously who need immediate, interpretable alerts rather than raw threshold breaches
- **Clinical informatics teams** evaluating ML-based monitoring tools as complements to rule-based alarm systems
- **Healthcare data scientists** exploring how statistical process control and LLM explanation can be layered in a clinical context
- **Health tech engineers** building or reviewing real-time streaming architectures for medical data

---

## How it works

```
Vital signs simulator (fixed seed, reproducible)
        │
        ▼
  FastAPI + SSE broadcaster
  ┌─────────────────────────────────────────────────┐
  │ readings_broadcaster  →  /stream                 │
  │ events_broadcaster    →  /events/stream          │
  └─────────────────────────────────────────────────┘
        │
        ▼
  Three-layer Detector (runs on every reading)
  ┌─────────────────────────────────────────────────┐
  │ Layer 1 — CUSUM (per vital, real-time)           │
  │   Accumulates normalised deviations              │
  │   Two-sided; resets and cools down on trigger    │
  │                                                  │
  │ Layer 2 — Z-score (per vital, rolling window)    │
  │   30-reading window; flags |z| > 3.0             │
  │                                                  │
  │ Layer 3 — Evidently (batch, every 60 readings)   │
  │   DataDriftPreset vs. reference window           │
  │   Detects distributional population shifts       │
  └─────────────────────────────────────────────────┘
        │
        ▼
  DetectionEvent persisted to SQLite
        │
        ▼
  Async Claude task (non-blocking)
  ┌─────────────────────────────────────────────────┐
  │ Structured prompt with vital context             │
  │ Returns: headline, explanation,                  │
  │          suggested_action, severity              │
  │ In-memory TTL cache (key: vital × detector ×    │
  │ severity bucket) prevents redundant API calls   │
  └─────────────────────────────────────────────────┘
        │
        ▼
  Enriched event pushed to SSE → live dashboard
```

The pub/sub broadcaster fans SSE messages out to all active connections. Slow consumers are dropped rather than backpressuring the stream. The Claude explanation task is created with `asyncio.create_task` — it never blocks the live vital stream.

---

## Drift timeline

The simulator injects three deterministic drift scenarios at fixed offsets from startup. This is documented openly — reproducibility is the point; the demo should always show the same detection behaviour.

| Time | Vital | Scenario | Detection layer expected |
|---|---|---|---|
| T+5 min | Heart Rate | Gradual upward drift: HR climbs +8 bpm over 4 minutes | CUSUM |
| T+12 min | SpO₂ | Sudden drop to ~93% (acute desaturation) | Z-score |
| T+20 min | Respiratory Rate | Distribution shift: mean moves from 16 → 22 breaths/min | Evidently |

Systolic BP and Temperature remain within normal ranges throughout.

---

## Example event output

```json
{
  "id": 47,
  "timestamp": "2025-04-09T10:34:17Z",
  "vital": "spo2",
  "detector": "zscore",
  "current_value": 92.4,
  "baseline_mean": 97.2,
  "baseline_sd": 1.1,
  "change_summary": "Spo2 reading of 92.4 % is 4.4 SD below the 60-second rolling mean",
  "severity": "high",
  "explanation": {
    "headline": "Acute SpO₂ desaturation detected: 92.4% — 4.4 SD below rolling mean.",
    "explanation": "A SpO₂ of 92.4% falls below the clinical threshold of 94%, indicating significant hypoxaemia. This magnitude of drop over a short window is inconsistent with measurement artefact and warrants immediate assessment. Possible causes include airway compromise, pulmonary deterioration, or equipment displacement.",
    "suggested_action": "Assess airway patency and breathing effort immediately; apply supplemental oxygen and reassess within 5 minutes.",
    "severity": "high"
  }
}
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI |
| Real-time | Server-Sent Events, asyncio pub/sub |
| Detection | CUSUM (custom), Z-score (NumPy), Evidently DataDriftPreset |
| AI explanations | Anthropic Claude API (claude-haiku-4-5) |
| Database | SQLite with WAL mode, SQLModel, aiosqlite |
| Data validation | Pydantic v2 |
| Frontend | Vanilla HTML/CSS/JS, dark-mode dashboard |
| Containerisation | Docker (multi-stage, non-root) |
| Deployment | Render |
| Package management | uv |

---

## Running locally

**With uv:**
```bash
git clone https://github.com/ThomasWaugh/clinical-data-monitor.git
cd clinical-data-monitor
cp .env.example .env          # add your ANTHROPIC_API_KEY
uv sync
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000 for the live dashboard, or http://localhost:8000/docs for the API.

The system works without an API key — anomaly events will be detected and streamed, but Claude explanations will be skipped.

**With Docker:**
```bash
docker build -t clinical-data-monitor .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=your-key-here clinical-data-monitor
```

**With Docker Compose (development, with hot reload):**
```bash
docker compose up
```

---

## Configuration

Copy `.env.example` to `.env`. Required:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key. If unset, explanations are skipped — detection still runs. |
| `DATABASE_URL` | SQLite connection string. Default: `sqlite+aiosqlite:///./monitor.db` |
| `ENVIRONMENT` | `development` or `production` |

Optional tuning (defaults shown):

| Variable | Default | Description |
|---|---|---|
| `READING_INTERVAL_SECONDS` | `2.0` | Simulator speed — seconds between readings |
| `CUSUM_H` | `5.0` | CUSUM decision threshold — higher = less sensitive |
| `CUSUM_K` | `0.5` | CUSUM slack (allowance before accumulation starts) |
| `ZSCORE_WINDOW` | `30` | Rolling window size for Z-score detector |
| `ZSCORE_THRESHOLD` | `3.0` | SD threshold for Z-score alert |
| `EVIDENTLY_WINDOW` | `60` | Batch window size for distribution comparison |
| `EVENT_COOLDOWN_SECONDS` | `30` | Minimum gap between events for the same vital + detector |
| `EXPLANATION_CACHE_TTL` | `300` | Seconds to cache Claude explanations (keyed by vital × detector × severity) |

---

## Running tests

```bash
uv run pytest
```

Tests cover core detection logic, LLM response parsing, and simulator vital generation:

```
tests/
├── test_detector.py    # CUSUM state machine, trigger and reset behaviour
├── test_explainer.py   # Claude response parsing, graceful degradation without API key
└── test_simulator.py   # Baseline vital ranges, drift injection at known timestamps
```

---

## Project structure

```
clinical-data-monitor/
├── app/
│   ├── main.py              # FastAPI app, SSE broadcaster, lifespan handler
│   ├── simulator.py         # Vital signs generator with deterministic drift injection
│   ├── detector.py          # Three-layer detector (CUSUM, Z-score, Evidently)
│   ├── explainer.py         # Claude API integration with caching
│   ├── models.py            # SQLModel database models and Pydantic schemas
│   ├── database.py          # Async SQLite engine and session management
│   ├── config.py            # Pydantic Settings configuration
│   ├── constants.py         # Physiological baseline values
│   ├── scripts/
│   │   └── seed.py          # Cold-start seeding (30 min of history)
│   ├── static/              # CSS, JS assets
│   └── templates/
│       └── dashboard.html   # Live monitoring dashboard
├── tests/
├── Dockerfile               # Multi-stage, non-root, production-ready
├── docker-compose.yml       # Development setup with hot reload
├── pyproject.toml
├── uv.lock
└── .env.example
```

---

## Limitations and responsible use

This tool is designed as a **technical demonstration**, not a clinical product. There are important limitations to understand:

**Synthetic data only.** The vital signs are generated by a NumPy simulator, not sourced from real patients. The drift scenarios are deterministic and scripted — the detector is not being evaluated against real clinical complexity.

**Detection is not validated clinically.** The CUSUM thresholds, Z-score window, and Evidently configuration have not been tuned against real patient data or validated against clinical outcomes. They are illustrative, not evidence-based.

**Claude explanations are not clinical advice.** The model generates plausible, structured explanations based on the alert data it receives. It has no access to the patient's history, medications, or full clinical picture. The explanations should be read as illustrative of the format — not as clinical guidance.

**Not a medical device.** This tool has not undergone clinical validation, regulatory review, or testing against established clinical decision support standards. It is a portfolio demonstration project.

---

## Background

Built by Tom Waugh as part of a health tech AI/ML portfolio. The architecture reflects the intersection of clinical informatics thinking (CUSUM is standard in healthcare quality monitoring; Evidently maps to real ML monitoring tooling) and modern async Python engineering. The three-layer detection approach — combining sequential, acute, and distributional signal — mirrors the multi-method monitoring strategy used in real ICU environments, where no single threshold is sufficient.

---

## Licence

MIT
