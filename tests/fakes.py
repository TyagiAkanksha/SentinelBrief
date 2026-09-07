"""`FakeLLMClient`: the only LLM double in the suite (CONVENTIONS.md §10, m0 task-03).

Replays queued responses (`str` or `Exception`) through the real `core.llm.parse_structured`
path so a bad JSON reply raises `StructuredOutputError` identically to the real
`worker.llm_client.OpenAICompatibleLLMClient`. Never hand-roll parsing here — that would let the
fake diverge from production behavior, which is exactly what CONVENTIONS.md §10 forbids.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel

from core.llm import ChatMessage, LLMResult, LLMUsage, parse_structured


@dataclass
class FakeCall:
    """One recorded call to `FakeLLMClient.complete_structured`."""

    messages: list[ChatMessage]
    response_model: type[BaseModel]
    model: str


class FakeLLMClient:
    """Test double for `core.llm.LLMClient`; replays canned replies in call order.

    Args:
        responses: Queue of replies. A `str` is validated through `parse_structured` (so bad
            JSON raises `StructuredOutputError` exactly like production); an `Exception`
            instance is raised as-is (so callers can simulate `LLMCallError`, etc.).
        usage: Token usage attached to every successful `LLMResult`.
        cost_usd: Cost attached to every successful `LLMResult`.
        latency_ms: Latency attached to every successful `LLMResult`.
    """

    def __init__(
        self,
        responses: Sequence[str | Exception],
        *,
        usage: LLMUsage = LLMUsage(100, 50),  # noqa: B008 -- frozen dataclass, effectively immutable
        cost_usd: Decimal = Decimal("0.000100"),
        latency_ms: int = 5,
    ) -> None:
        """Queue the canned responses and record the fixed usage/cost/latency to attach."""
        self._responses: list[str | Exception] = list(responses)
        self._usage = usage
        self._cost_usd = cost_usd
        self._latency_ms = latency_ms
        self.calls: list[FakeCall] = []

    async def complete_structured[T: BaseModel](
        self,
        *,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        model: str,
    ) -> LLMResult[T]:
        """Record the call, then pop and replay the next queued response.

        Raises:
            AssertionError: The response queue is exhausted — the test asked for more calls
                than it queued replies for.
        """
        self.calls.append(
            FakeCall(messages=list(messages), response_model=response_model, model=model)
        )
        if not self._responses:
            raise AssertionError("FakeLLMClient: no responses left")
        next_response = self._responses.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        return parse_structured(
            next_response,
            response_model,
            model=model,
            usage=self._usage,
            cost_usd=self._cost_usd,
            latency_ms=self._latency_ms,
        )
