"""Shared constants — importable by any module without circular dependency risk."""

VITALS_BASELINE = {
    "heart_rate":       {"mean": 72.0,  "sd": 4.0,  "unit": "bpm"},
    "spo2":             {"mean": 97.0,  "sd": 0.8,  "unit": "%"},
    "systolic_bp":      {"mean": 120.0, "sd": 6.0,  "unit": "mmHg"},
    "respiratory_rate": {"mean": 16.0,  "sd": 2.0,  "unit": "breaths/min"},
    "temperature":      {"mean": 37.0,  "sd": 0.15, "unit": "°C"},
}
