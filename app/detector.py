"""
Two-layer drift and anomaly detection:

  Layer 1 — CUSUM (per vital, real-time)
    Detects small persistent shifts (gradual drift).
    Two-sided CUSUM; resets on trigger and applies a cooldown.

  Layer 2 — Rolling Z-score (per vital, real-time)
    Detects acute single-reading anomalies (sudden SpO2 drop).
    Uses a 30-reading window; flags |z| > 3.0.

  Layer 3 — Evidently distribution drift (windowed batch)
    Runs every 60 readings against the first 60 readings as reference.
    Signals distributional shift across all vitals.
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from app.config import settings
from app.constants import VITALS_BASELINE

VITAL_KEYS = list(VITALS_BASELINE.keys())


class CUSUMState:
    """Two-sided CUSUM for one vital sign (operates on normalised values)."""

    def __init__(self, mean: float, sd: float, h: float, k: float):
        self.mean = mean
        self.sd = sd
        self.h = h
        self.k = k
        self.s_pos = 0.0
        self.s_neg = 0.0

    def update(self, value: float) -> Optional[str]:
        """Returns 'up' or 'down' if a change is detected, else None."""
        z = (value - self.mean) / self.sd
        self.s_pos = max(0.0, self.s_pos + z - self.k)
        self.s_neg = max(0.0, self.s_neg - z - self.k)
        if self.s_pos > self.h:
            self.reset()
            return "up"
        if self.s_neg > self.h:
            self.reset()
            return "down"
        return None

    def reset(self):
        self.s_pos = 0.0
        self.s_neg = 0.0


class Detector:
    def __init__(self):
        h = settings.cusum_h
        k = settings.cusum_k

        self._cusum: dict[str, CUSUMState] = {
            vital: CUSUMState(
                mean=VITALS_BASELINE[vital]["mean"],
                sd=VITALS_BASELINE[vital]["sd"],
                h=h,
                k=k,
            )
            for vital in VITAL_KEYS
        }

        self._zscore_windows: dict[str, deque] = {
            vital: deque(maxlen=settings.zscore_window) for vital in VITAL_KEYS
        }

        # Cooldown tracking: last event time per (vital, detector)
        self._last_event: dict[tuple, datetime] = {}

        # Buffer for Evidently — stores raw readings as dicts
        self._evidently_buffer: list[dict] = []
        self._reference_window: Optional[list[dict]] = None
        self._reading_count = 0

    def _is_on_cooldown(self, vital: str, detector: str) -> bool:
        key = (vital, detector)
        last = self._last_event.get(key)
        if last is None:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < settings.event_cooldown_seconds

    def _record_event_time(self, vital: str, detector: str):
        self._last_event[(vital, detector)] = datetime.now(timezone.utc)

    async def process(self, reading, events_broadcaster) -> None:
        from app.models import DetectionEvent
        from app.database import engine
        from sqlmodel.ext.asyncio.session import AsyncSession
        from sqlalchemy.orm import sessionmaker

        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        raw = {v: getattr(reading, v) for v in VITAL_KEYS}
        self._reading_count += 1

        detected_events: list[dict] = []

        for vital in VITAL_KEYS:
            value = raw[vital]
            baseline = VITALS_BASELINE[vital]

            # ── CUSUM ──
            if not self._is_on_cooldown(vital, "cusum"):
                direction = self._cusum[vital].update(value)
                if direction:
                    self._record_event_time(vital, "cusum")
                    diff = value - baseline["mean"]
                    change = f"{vital.replace('_', ' ').title()} drifted {direction} by {abs(diff):.1f} {baseline['unit']} from baseline"
                    detected_events.append({
                        "vital": vital,
                        "detector": "cusum",
                        "current_value": value,
                        "baseline_mean": baseline["mean"],
                        "baseline_sd": baseline["sd"],
                        "change_summary": change,
                    })
            else:
                # Still update CUSUM state even during cooldown
                self._cusum[vital].update(value)

            # ── Z-score ──
            window = self._zscore_windows[vital]
            window.append(value)
            if len(window) >= settings.zscore_window and not self._is_on_cooldown(vital, "zscore"):
                arr = np.array(window)
                z = (value - arr.mean()) / (arr.std() + 1e-9)
                if abs(z) > settings.zscore_threshold:
                    self._record_event_time(vital, "zscore")
                    direction = "above" if z > 0 else "below"
                    change = (
                        f"{vital.replace('_', ' ').title()} reading of {value} {baseline['unit']} "
                        f"is {abs(z):.1f} SD {direction} the 60-second rolling mean"
                    )
                    detected_events.append({
                        "vital": vital,
                        "detector": "zscore",
                        "current_value": value,
                        "baseline_mean": float(arr.mean()),
                        "baseline_sd": float(arr.std()),
                        "change_summary": change,
                    })

        # ── Evidently (batch, every N readings) ──
        self._evidently_buffer.append(raw)
        if self._reading_count == settings.evidently_window:
            self._reference_window = list(self._evidently_buffer)

        if (
            self._reading_count > settings.evidently_window
            and self._reading_count % settings.evidently_window == 0
            and self._reference_window is not None
        ):
            evidently_events = await self._run_evidently(self._evidently_buffer[-settings.evidently_window:])
            detected_events.extend(evidently_events)

        # Persist and publish all detected events
        for ev_data in detected_events:
            async with async_session() as session:
                event = DetectionEvent(
                    timestamp=reading.timestamp,
                    **ev_data,
                )
                session.add(event)
                await session.commit()
                await session.refresh(event)

            event_payload = {
                "id": event.id,
                "timestamp": event.timestamp.isoformat(),
                **ev_data,
                "severity": None,
                "explanation": None,
            }
            events_broadcaster.publish(event_payload)

            # Trigger async LLM explanation — does not block the stream
            asyncio.create_task(
                _generate_explanation(event.id, reading, ev_data, events_broadcaster)
            )

    async def _run_evidently(self, current_window: list[dict]) -> list[dict]:
        """Run an Evidently data drift report. Returns a list of event dicts for drifted vitals."""
        import pandas as pd
        from evidently.report import Report
        from evidently.metric_preset import DataDriftPreset

        try:
            ref_df = pd.DataFrame(self._reference_window)
            cur_df = pd.DataFrame(current_window)

            report = Report(metrics=[DataDriftPreset()])
            report.run(reference_data=ref_df, current_data=cur_df)
            result = report.as_dict()

            drifted_events = []
            drift_results = result.get("metrics", [])
            for metric in drift_results:
                if metric.get("metric") != "ColumnDriftMetric":
                    continue
                col = metric["result"].get("column_name")
                if col not in VITAL_KEYS:
                    continue
                if not metric["result"].get("drift_detected", False):
                    continue
                if self._is_on_cooldown(col, "evidently"):
                    continue
                self._record_event_time(col, "evidently")
                ref_mean = float(pd.DataFrame(self._reference_window)[col].mean())
                cur_mean = float(pd.DataFrame(current_window)[col].mean())
                ref_sd = float(pd.DataFrame(self._reference_window)[col].std())
                baseline = VITALS_BASELINE[col]
                change = (
                    f"Distribution drift detected in {col.replace('_', ' ')}: "
                    f"current mean {cur_mean:.1f} vs reference mean {ref_mean:.1f} {baseline['unit']}"
                )
                drifted_events.append({
                    "vital": col,
                    "detector": "evidently",
                    "current_value": cur_mean,
                    "baseline_mean": ref_mean,
                    "baseline_sd": ref_sd,
                    "change_summary": change,
                })
            return drifted_events
        except Exception:
            return []


async def _generate_explanation(
    event_id: int,
    reading,
    ev_data: dict,
    events_broadcaster,
) -> None:
    """Async task: call Claude, persist explanation, push updated event to SSE queue."""
    from app.explainer import explain_event
    from app.models import Explanation
    from app.database import engine
    from sqlmodel.ext.asyncio.session import AsyncSession
    from sqlalchemy.orm import sessionmaker

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        result = await explain_event(ev_data, reading)
        if result is None:
            return

        explanation = Explanation(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc),
            **result,
        )
        async with async_session() as session:
            session.add(explanation)
            await session.commit()
            await session.refresh(explanation)

        # Update event severity in DB
        from app.models import DetectionEvent
        async with async_session() as session:
            event = await session.get(DetectionEvent, event_id)
            if event:
                event.severity = result["severity"]
                session.add(event)
                await session.commit()

        # Push enriched event update to SSE
        enriched = {
            "id": event_id,
            "timestamp": reading.timestamp.isoformat(),
            **ev_data,
            "severity": result["severity"],
            "explanation": {
                "headline": result["headline"],
                "explanation": result["explanation"],
                "suggested_action": result["suggested_action"],
                "severity": result["severity"],
            },
        }
        events_broadcaster.publish(enriched)

    except Exception:
        pass
