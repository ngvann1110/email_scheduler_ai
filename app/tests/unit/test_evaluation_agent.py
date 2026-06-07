"""
Unit tests for app/agents/evaluation_agent.py

Tests:
- evaluate_and_retry() — retry logic
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.evaluation_agent import evaluate_and_retry


class FakePipeline:
    """Simulate a pipeline that returns a dict result."""

    def __init__(self, fail_count: int = 0):
        self.call_count = 0
        self.fail_count = fail_count

    async def __call__(self, email) -> dict:
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise Exception("Temporary failure")
        return {"type": "schedule_flow", "data": {"email": {"intent": "schedule"}}}


class TestEvaluateAndRetry:
    """Tests for evaluate_and_retry()."""

    @pytest.mark.asyncio
    async def test_evaluate_and_retry_success_first_try(self):
        """Should return result immediately when pipeline succeeds."""
        pipeline = FakePipeline()

        result = await evaluate_and_retry(pipeline, MagicMock())

        assert result["type"] == "schedule_flow"
        assert pipeline.call_count == 1

    @pytest.mark.asyncio
    async def test_evaluate_and_retry_eventually_succeeds(self):
        """Should retry and eventually succeed after failures."""
        pipeline = FakePipeline(fail_count=2)

        with patch("app.agents.evaluation_agent.asyncio.sleep"):
            result = await evaluate_and_retry(pipeline, MagicMock())

        assert result["type"] == "schedule_flow"
        assert pipeline.call_count == 3  # 2 fails + 1 success

    @pytest.mark.asyncio
    async def test_evaluate_and_retry_max_retries_exceeded(self):
        """Should return last result after max retries exceeded (does not raise)."""
        pipeline = FakePipeline(fail_count=10)

        with patch("app.agents.evaluation_agent.asyncio.sleep"):
            result = await evaluate_and_retry(pipeline, MagicMock())

        # After max retries, returns the last (failed) result, doesn't raise
        assert result is not None
        assert pipeline.call_count == 3  # default MAX_RETRIES=2 → 3 attempts
