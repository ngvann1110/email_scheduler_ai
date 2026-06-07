import asyncio
import logging
from typing import Awaitable, Callable

from app.agents.chat_agent import evaluate_email
from app.core.logger import log_event

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_DELAY = 2  # seconds


def _evaluate_result(pipeline_result: dict) -> tuple[bool, str]:
    """Evaluate pipeline result and return (is_acceptable, reason).

    Uses the chat_agent LLM to judge whether the pipeline output
    is satisfactory.  This is a pure sync function because ``evaluate_email``
    is synchronous.
    """
    try:
        evaluation = evaluate_email(pipeline_result)
        is_acceptable = evaluation.get("acceptable", True)
        reason = evaluation.get("reason", "")
        return is_acceptable, reason
    except Exception as e:
        logger.warning(
            "[EvaluationAgent] LLM evaluation failed: %s – defaulting to acceptable", e)
        return True, "evaluation skipped (LLM error)"


async def evaluate_and_retry(
    pipeline_fn: Callable[..., Awaitable[dict]],
    email,
    max_retries: int = MAX_RETRIES,
) -> dict:
    """Run *pipeline_fn* and retry up to *max_retries* times if the
    evaluation agent deems the result unacceptable.

    Parameters
    ----------
    pipeline_fn : Callable[..., Awaitable[dict]]
        Async pipeline to execute (e.g. ``run_pipeline``).
    email : EmailSchema
        The incoming email object.
    max_retries : int
        Maximum retry attempts (default 2).

    Returns
    -------
    dict
        The pipeline result from the first acceptable attempt, or the
        last attempt if none were acceptable.
    """
    last_result: dict | None = None

    for attempt in range(max_retries + 1):
        logger.info(
            "[EvaluationAgent] Attempt %d/%d | email=%s",
            attempt + 1, max_retries + 1, email.subject,
        )

        try:
            result = await pipeline_fn(email)
            last_result = result
        except Exception as e:
            logger.warning(
                "[EvaluationAgent] Pipeline error at attempt %d: %s", attempt + 1, e)
            log_event(
                agent="evaluation_agent",
                status="error",
                payload={
                    "attempt": attempt + 1,
                    "error": str(e),
                },
            )
            if attempt < max_retries:
                await asyncio.sleep(RETRY_DELAY)
            continue

        acceptable, reason = _evaluate_result(result)

        log_event(
            agent="evaluation_agent",
            status="acceptable" if acceptable else "retry",
            payload={
                "attempt": attempt + 1,
                "acceptable": acceptable,
                "reason": reason,
                "flow": result.get("type"),
            },
        )

        if acceptable:
            logger.info(
                "[EvaluationAgent] ✓ Result acceptable at attempt %d", attempt + 1)
            return result

        logger.warning(
            "[EvaluationAgent] ✗ Result unacceptable | reason=%s | retrying...",
            reason,
        )

        if attempt < max_retries:
            await asyncio.sleep(RETRY_DELAY)

    logger.warning(
        "[EvaluationAgent] ⚠ Max retries exhausted, returning last result",
    )
    if last_result is None:
        return {
            "type": "error",
            "status": "max_retries_exhausted",
            "message": "Pipeline failed all retry attempts",
        }
    return last_result
