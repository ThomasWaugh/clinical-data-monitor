"""
Claude API integration for generating clinical explanations of detected events.

Design principles:
  - Structured JSON output enforced via tool use / response schema
  - Async — never called in the hot SSE path
  - In-memory cache keyed by (vital, detector, severity_bucket) with TTL
  - Claude is given the data; it interprets, it does not invent
"""

import asyncio
import json
import time
from typing import Optional

import anthropic

from app.config import settings
from app.constants import VITALS_BASELINE

SYSTEM_PROMPT = """\
You are a clinical informatics assistant helping ICU nurses and attending physicians \
understand real-time patient monitoring alerts. Your explanations must be:
- Written for a clinician, not a data scientist
- Factually grounded in the data provided — never speculate beyond it
- Concise (3-5 sentences maximum)
- Clinically actionable where appropriate
You will always respond with valid JSON matching the schema provided."""

USER_PROMPT_TEMPLATE = """\
A monitoring alert has been triggered for patient vitals.

Alert type: {detector_type}
Vital sign: {vital_display}
Time of alert: {timestamp}
Detected change: {change_summary}
Current value: {current_value} {unit}
Reference baseline: {baseline_mean} ± {baseline_sd} {unit}

Respond with JSON in exactly this format:
{{
  "headline": "<one sentence: what happened>",
  "explanation": "<2-3 sentences: clinical significance of this change>",
  "suggested_action": "<one sentence: what a clinician should consider>",
  "severity": "low" or "medium" or "high"
}}"""

DETECTOR_DISPLAY = {
    "cusum": "CUSUM drift detection (persistent gradual change)",
    "zscore": "Z-score anomaly detection (acute single-reading spike)",
    "evidently": "Distribution drift report (windowed statistical comparison)",
}

# ── In-memory explanation cache ───────────────────────────────────────────────
# Key: (vital, detector, severity_bucket)   Value: (result_dict, expiry_time)
_cache: dict[tuple, tuple[dict, float]] = {}
_cache_lock = asyncio.Lock()


def _severity_bucket(current: float, mean: float, sd: float) -> str:
    if sd == 0:
        return "medium"
    z = abs(current - mean) / sd
    if z < 2.0:
        return "low"
    if z < 4.0:
        return "medium"
    return "high"


async def explain_event(ev_data: dict, reading) -> Optional[dict]:
    """
    Generate a clinical explanation for a detection event using Claude.
    Returns a dict with keys: headline, explanation, suggested_action, severity.
    Returns None if the API key is not configured.
    """
    if not settings.anthropic_api_key:
        return None

    vital = ev_data["vital"]
    detector = ev_data["detector"]
    current = ev_data["current_value"]
    b_mean = ev_data["baseline_mean"]
    b_sd = ev_data["baseline_sd"]
    change_summary = ev_data["change_summary"]

    bucket = _severity_bucket(current, b_mean, b_sd)
    cache_key = (vital, detector, bucket)

    async with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and time.time() < cached[1]:
            return cached[0]

    baseline = VITALS_BASELINE.get(vital, {})
    unit = baseline.get("unit", "")
    vital_display = vital.replace("_", " ").title()
    detector_display = DETECTOR_DISPLAY.get(detector, detector)

    prompt = USER_PROMPT_TEMPLATE.format(
        detector_type=detector_display,
        vital_display=vital_display,
        timestamp=reading.timestamp.isoformat(),
        change_summary=change_summary,
        current_value=current,
        unit=unit,
        baseline_mean=b_mean,
        baseline_sd=round(b_sd, 2),
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)
        # Validate expected keys
        required = {"headline", "explanation", "suggested_action", "severity"}
        if not required.issubset(result.keys()):
            return None
        if result["severity"] not in ("low", "medium", "high"):
            result["severity"] = bucket

        async with _cache_lock:
            _cache[cache_key] = (result, time.time() + settings.explanation_cache_ttl)

        return result

    except Exception:
        return None
