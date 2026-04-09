import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Depends, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy import desc

from app.config import settings
from app.database import init_db, get_session
from app.models import VitalReading, DetectionEvent, Explanation, DetectionEventOut, ExplanationOut

APP_DIR = Path(__file__).parent


# ── Pub/sub broadcaster ───────────────────────────────────────────────────────
# Each SSE connection registers a queue here. The simulator calls broadcast()
# which fans out to every active subscriber. When a connection closes its queue
# is removed, so no stale tasks accumulate.

class Broadcaster:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    def publish(self, payload: dict):
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow consumer — drop rather than block


readings_broadcaster = Broadcaster()
events_broadcaster = Broadcaster()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    from app.scripts.seed import seed_if_empty
    await seed_if_empty()

    from app.simulator import run_simulator
    sim_task = asyncio.create_task(
        run_simulator(readings_broadcaster, events_broadcaster)
    )

    yield

    sim_task.cancel()
    try:
        await sim_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Clinical Data Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


# ── Data endpoints ────────────────────────────────────────────────────────────

@app.get("/readings")
async def get_readings(
    limit: int = 150,
    session: AsyncSession = Depends(get_session),
):
    result = await session.exec(
        select(VitalReading).order_by(desc(VitalReading.timestamp)).limit(limit)
    )
    rows = result.all()
    return [r.model_dump() for r in reversed(rows)]


@app.get("/events")
async def get_events(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    result = await session.exec(
        select(DetectionEvent).order_by(desc(DetectionEvent.timestamp)).limit(limit)
    )
    events = result.all()
    out = []
    for ev in reversed(events):
        exp_result = await session.exec(
            select(Explanation).where(Explanation.event_id == ev.id).limit(1)
        )
        exp = exp_result.first()
        out.append(
            DetectionEventOut(
                **ev.model_dump(),
                explanation=ExplanationOut(**exp.model_dump()) if exp else None,
            ).model_dump()
        )
    return out


# ── SSE streams ───────────────────────────────────────────────────────────────

async def _sse_generator(
    broadcaster: Broadcaster,
) -> AsyncGenerator[str, None]:
    """Subscribe to a broadcaster, yield SSE messages, unsubscribe on disconnect.
    Sends a keepalive comment every 30 s to prevent Render's idle timeout."""
    q = broadcaster.subscribe()
    last_keepalive = asyncio.get_event_loop().time()
    try:
        while True:
            now = asyncio.get_event_loop().time()
            if now - last_keepalive >= 30:
                yield ": keepalive\n\n"
                last_keepalive = now
            try:
                payload = await asyncio.wait_for(q.get(), timeout=1.0)
                yield f"data: {json.dumps(payload)}\n\n"
            except asyncio.TimeoutError:
                continue
    finally:
        broadcaster.unsubscribe(q)


@app.get("/stream")
async def stream_readings():
    return StreamingResponse(
        _sse_generator(readings_broadcaster),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/events/stream")
async def stream_events():
    return StreamingResponse(
        _sse_generator(events_broadcaster),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
