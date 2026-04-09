from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class VitalReading(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(index=True)
    heart_rate: float
    spo2: float
    systolic_bp: float
    respiratory_rate: float
    temperature: float
    # Flags whether this reading was during an injected drift window
    drift_active: bool = False


class DetectionEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(index=True)
    vital: str           # e.g. "heart_rate"
    detector: str        # "cusum" | "zscore" | "evidently"
    current_value: float
    baseline_mean: float
    baseline_sd: float
    change_summary: str
    severity: Optional[str] = None   # populated after LLM call


class Explanation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(index=True, foreign_key="detectionevent.id")
    timestamp: datetime
    headline: str
    explanation: str
    suggested_action: str
    severity: str        # "low" | "medium" | "high"


# --- Pydantic response schemas (not DB tables) ---

class VitalReadingOut(SQLModel):
    id: int
    timestamp: datetime
    heart_rate: float
    spo2: float
    systolic_bp: float
    respiratory_rate: float
    temperature: float
    drift_active: bool


class DetectionEventOut(SQLModel):
    id: int
    timestamp: datetime
    vital: str
    detector: str
    current_value: float
    baseline_mean: float
    baseline_sd: float
    change_summary: str
    severity: Optional[str]
    explanation: Optional["ExplanationOut"] = None


class ExplanationOut(SQLModel):
    id: int
    event_id: int
    timestamp: datetime
    headline: str
    explanation: str
    suggested_action: str
    severity: str
