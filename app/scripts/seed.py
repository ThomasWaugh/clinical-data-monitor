"""
Cold-start seeding: populates the DB with 30 minutes of historical readings
so the dashboard shows a meaningful chart on first load rather than a blank page.
Runs automatically on startup if the readings table is empty.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models import VitalReading
from app.simulator import VITALS_BASELINE, _sample_vitals


async def seed_if_empty() -> None:
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        result = await session.exec(select(VitalReading).limit(1))
        if result.first() is not None:
            return  # already has data

    print("Seeding demo data (30 minutes of history)...")
    interval = 2.0  # seconds per reading
    total_readings = int(30 * 60 / interval)  # 900 readings
    rng = np.random.default_rng(seed=99)   # different seed to simulator's live seed
    start_time = datetime.now(timezone.utc) - timedelta(seconds=total_readings * interval)

    readings = []
    for i in range(total_readings):
        elapsed = i * interval
        values, drift_active = _sample_vitals(elapsed, rng)
        readings.append(
            VitalReading(
                timestamp=start_time + timedelta(seconds=elapsed),
                drift_active=drift_active,
                **values,
            )
        )

    # Bulk insert in batches of 200
    async with async_session() as session:
        for i in range(0, len(readings), 200):
            session.add_all(readings[i : i + 200])
            await session.commit()

    print(f"Seeded {total_readings} readings.")
