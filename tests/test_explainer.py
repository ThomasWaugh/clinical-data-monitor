"""Tests for the LLM explainer — uses mocked Claude responses."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.explainer import explain_event, _severity_bucket


class TestSeverityBucket:
    def test_low_severity_small_deviation(self):
        assert _severity_bucket(74.0, 72.0, 4.0) == "low"   # z=0.5

    def test_medium_severity_moderate_deviation(self):
        assert _severity_bucket(80.0, 72.0, 4.0) == "medium"  # z=2.0

    def test_high_severity_large_deviation(self):
        assert _severity_bucket(90.0, 72.0, 4.0) == "high"   # z=4.5


class TestExplainEvent:
    @pytest.fixture
    def ev_data(self):
        return {
            "vital": "heart_rate",
            "detector": "cusum",
            "current_value": 82.0,
            "baseline_mean": 72.0,
            "baseline_sd": 4.0,
            "change_summary": "Heart rate drifted up by 10.0 bpm from baseline",
        }

    @pytest.fixture
    def mock_reading(self):
        reading = MagicMock()
        reading.timestamp = MagicMock()
        reading.timestamp.isoformat.return_value = "2026-04-08T10:00:00+00:00"
        return reading

    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(self, ev_data, mock_reading):
        with patch("app.explainer.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            result = await explain_event(ev_data, mock_reading)
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_valid_claude_response(self, ev_data, mock_reading):
        mock_response = {
            "headline": "Heart rate has increased significantly above baseline.",
            "explanation": "The patient's heart rate has drifted upward by 10 bpm over the past several minutes, consistent with developing tachycardia. This pattern may indicate pain, anxiety, fluid deficit, or early haemodynamic instability.",
            "suggested_action": "Consider reviewing fluid status, pain score, and recent medications.",
            "severity": "medium",
        }

        mock_content = MagicMock()
        mock_content.text = json.dumps(mock_response)

        mock_message = MagicMock()
        mock_message.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch("app.explainer.settings") as mock_settings, \
             patch("app.explainer.anthropic.AsyncAnthropic", return_value=mock_client), \
             patch("app.explainer._cache", {}):
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.explanation_cache_ttl = 300
            result = await explain_event(ev_data, mock_reading)

        assert result is not None
        assert result["severity"] == "medium"
        assert "headline" in result
        assert "explanation" in result
        assert "suggested_action" in result

    @pytest.mark.asyncio
    async def test_handles_malformed_response_gracefully(self, ev_data, mock_reading):
        mock_content = MagicMock()
        mock_content.text = "This is not JSON"

        mock_message = MagicMock()
        mock_message.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch("app.explainer.settings") as mock_settings, \
             patch("app.explainer.anthropic.AsyncAnthropic", return_value=mock_client), \
             patch("app.explainer._cache", {}):
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.explanation_cache_ttl = 300
            result = await explain_event(ev_data, mock_reading)

        assert result is None
