"""Tests for the vital signs simulator."""

import numpy as np
import pytest

from app.simulator import _sample_vitals, DRIFT_START_HR, DRIFT_END_HR, DRIFT_SPO2, DRIFT_RR_START
from app.constants import VITALS_BASELINE


def make_rng():
    return np.random.default_rng(seed=0)


class TestBaselineVitals:
    def test_normal_values_within_expected_range(self):
        rng = make_rng()
        for _ in range(100):
            values, drift = _sample_vitals(0.0, rng)
            assert not drift
            assert 40 < values["heart_rate"] < 140
            assert 80 < values["spo2"] <= 100
            assert 80 < values["systolic_bp"] < 200
            assert 4 < values["respiratory_rate"] < 35
            assert 35.0 < values["temperature"] < 40.0

    def test_no_drift_before_first_scenario(self):
        rng = make_rng()
        values, drift = _sample_vitals(1.0, rng)
        assert not drift


class TestDriftInjection:
    def test_hr_drift_active_during_window(self):
        rng = make_rng()
        # Mid-way through HR drift window
        mid = (DRIFT_START_HR + DRIFT_END_HR) / 2
        values, drift = _sample_vitals(mid, rng)
        assert drift
        # Mean HR should be meaningfully above baseline
        samples = [_sample_vitals(mid, make_rng())[0]["heart_rate"] for _ in range(200)]
        assert np.mean(samples) > VITALS_BASELINE["heart_rate"]["mean"] + 2

    def test_spo2_drops_after_drift_event(self):
        rng = make_rng()
        samples = [_sample_vitals(DRIFT_SPO2 + 10, make_rng())[0]["spo2"] for _ in range(200)]
        assert np.mean(samples) < 95.0  # well below normal ~97%

    def test_rr_shifts_after_distribution_change(self):
        rng = make_rng()
        samples = [_sample_vitals(DRIFT_RR_START + 10, make_rng())[0]["respiratory_rate"] for _ in range(200)]
        assert np.mean(samples) > 18.0  # shifted from baseline 16

    def test_drift_flag_set_correctly(self):
        rng = make_rng()
        _, before = _sample_vitals(DRIFT_START_HR - 1, rng)
        rng2 = make_rng()
        _, during = _sample_vitals(DRIFT_START_HR + 1, rng2)
        assert not before
        assert during
