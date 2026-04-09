"""
Vital signs simulator.

Generates a deterministic stream of synthetic patient vitals at a fixed interval,
with three drift scenarios injected at known timestamps relative to simulator start.
This is documented openly in the README -- deterministic injection is intentional
so the demo is always reproducible.

Drift scenarios:
  T+5min  -> Gradual HR drift: heart rate climbs +0.5 bpm/reading over 4 minutes
  T+12min -> Sudden SpO2 drop: SpO2 drops to ~93% (acute desaturation)
  T+20min -> Respiratory rate distribution shift: mean shifts from 16 -> 22
"""

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

from app.config import settings
from app.constants import VITALS_BASELINE
from app.models import VitalReading

# ---- Drift timeline (seconds from simulator start) --------------------------

DRIFT_START_HR = 5 * 60     # T+5min: gradual HR drift begins
DRIFT_END_HR   = 9 * 60     # T+9min: HR drift ends (~8 bpm higher)
DRIFT_SPO2     = 12 * 60    # T+12min: sudden SpO2 drop
DRIFT_RR_START = 20 * 60    # T+20min: RR distribution shift


def _sample_vitals(elapsed: float, rng: np.random.Generator) -> tuple[dict, bool]:
    """
    Return a dict of vital values for the current time offset and a flag
    indicating whether any drift is currently active.
    """
    b = VITALS_BASELINE
    drift_active = False

    # Heart rate: gradual drift between T+5m and T+9m
    if DRIFT_START_HR <= elapsed <= DRIFT_END_HR:
        drift_active = True
        progress = (elapsed - DRIFT_START_HR) / (DRIFT_END_HR - DRIFT_START_HR)
        hr_mean = b["heart_rate"]["mean"] + progress * 8.0
    else:
        hr_mean = b["heart_rate"]["mean"]

    hr = float(rng.normal(hr_mean, b["heart_rate"]["sd"]))

    # SpO2: sudden drop from T+12min onwards
    if elapsed >= DRIFT_SPO2:
        drift_active = True
        spo2_mean = 93.0
        spo2_sd = 1.0
    else:
        spo2_mean = b["spo2"]["mean"]
        spo2_sd = b["spo2"]["sd"]
    spo2 = float(np.clip(rng.normal(spo2_mean, spo2_sd), 70.0, 100.0))

    # Systolic BP: always normal
    sbp = float(rng.normal(b["systolic_bp"]["mean"], b["systolic_bp"]["sd"]))

    # Respiratory rate: distribution shift from T+20min
    if elapsed >= DRIFT_RR_START:
        drift_active = True
        rr_mean = 22.0
        rr_sd = 2.5
    else:
        rr_mean = b["respiratory_rate"]["mean"]
        rr_sd = b["respiratory_rate"]["sd"]
    rr = float(np.clip(rng.normal(rr_mean, rr_sd), 4.0, 50.0))

    # Temperature: always normal
    temp = float(rng.normal(b["temperature"]["mean"], b["temperature"]["sd"]))

    return {
        "heart_rate": round(hr, 1),
        "spo2": round(spo2, 1),
        "systolic_bp": round(sbp, 1),
        "respiratory_rate": round(rr, 1),
        "temperature": round(temp, 2),
    }, drift_active


async def run_simulator(
    readings_broadcaster,
    events_broadcaster,
) -> None:
    """
    Main simulator loop. Runs as a background asyncio task.
    Generates a reading every `reading_interval_seconds`, persists it,
    pushes it to the SSE queue, and runs drift/anomaly detection.
    """
    from app.detector import Detector  # deferred import avoids any remaining circular risk
    from sqlmodel.ext.asyncio.session import AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.database import engine

    rng = np.random.default_rng(seed=42)   # fixed seed -> reproducible demo
    detector = Detector()
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    elapsed = 0.0

    while True:
        start = asyncio.get_event_loop().time()

        try:
            values, drift_active = _sample_vitals(elapsed, rng)
            now = datetime.now(timezone.utc)

            reading = VitalReading(
                timestamp=now,
                drift_active=drift_active,
                **values,
            )

            async with async_session() as session:
                session.add(reading)
                await session.commit()
                await session.refresh(reading)

            # Broadcast to all SSE reading subscribers
            payload = {
                "id": reading.id,
                "timestamp": now.isoformat(),
                "drift_active": drift_active,
                **values,
            }
            readings_broadcaster.publish(payload)

            # Run detection; events are broadcast to events subscribers by the detector
            await detector.process(reading, events_broadcaster)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Simulator loop error (will retry): %s", e)

        elapsed += settings.reading_interval_seconds
        taken = asyncio.get_event_loop().time() - start
        sleep_for = max(0.0, settings.reading_interval_seconds - taken)
        await asyncio.sleep(sleep_for)
