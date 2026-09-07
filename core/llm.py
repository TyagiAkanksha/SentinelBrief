"""LLMClient Protocol + result/usage types (CONVENTIONS.md §2, PRD §4/§6.5/§10.1).

This module is the provider-neutral seam between `worker/` and any LLM backend: a `Protocol`,
plain dataclasses, and two pure helpers (`parse_structured`, `compute_cost_usd`). It carries **no
SDK import** — `worker/llm_client.py` is the only module that imports `openai` (import-linter
contract 3). `tests/fakes.py::FakeLLMClient` replays canned replies through `parse_structured` so
a malformed reply fails identically in tests and production (CONVENTIONS.md §10).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Protocol, TypedDict

from pydantic import BaseModel, ValidationError

from core.config import ModelPrice
from core.errors import StructuredOutputError


class ChatMessage(TypedDict):
    """One chat message sent to an LLM (PRD §4/§6.5)."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMUsage:
    """Token counts consumed by one LLM call, used for cost accounting (PRD §6.4)."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    """The validated outcome of one structured-output LLM call (PRD §6.5)."""

    parsed: T
    raw_text: str
    model: str
    usage: LLMUsage
    cost_usd: Decimal
    latency_ms: int


class LLMClient(Protocol):
    """Provider-neutral seam every LLM backend (real or fake) implements.

    `worker.llm_client.OpenAICompatibleLLMClient` and `tests.fakes.FakeLLMClient` are the two
    implementations; callers (e.g. `worker.triage.TriagePipeline`) only ever depend on this
    Protocol, never on a concrete SDK type (PRD §10.1).
    """

    async def complete_structured[T: BaseModel](
        self, *, messages: Sequence[ChatMessage], response_model: type[T], model: str
    ) -> LLMResult[T]:
        """Send `messages` to `model` and validate the reply as `response_model`.

        Args:
            messages: The chat messages to send, in order.
            response_model: The Pydantic model the reply must validate against.
            model: The model id to call.

        Returns:
            An `LLMResult` carrying the parsed model plus raw text, usage, cost and latency.

        Raises:
            LLMCallError: The underlying call failed (network, HTTP error, missing usage/content).
            StructuredOutputError: The reply text failed to validate as `response_model`.
        """
        ...


def parse_structured[T: BaseModel](
    raw_text: str,
    response_model: type[T],
    *,
    model: str,
    usage: LLMUsage,
    cost_usd: Decimal,
    latency_ms: int,
) -> LLMResult[T]:
    """Validate `raw_text` as `response_model`, carrying usage/cost/latency through either path.

    Both the real client and `FakeLLMClient` call this so a malformed reply raises the identical
    `StructuredOutputError` in tests and production (CONVENTIONS.md §10).

    Args:
        raw_text: The raw LLM reply text (expected to be a single JSON object).
        response_model: The Pydantic model the reply must validate against.
        model: The model id that produced `raw_text`.
        usage: Token usage for the call that produced `raw_text`.
        cost_usd: Cost in USD of the call that produced `raw_text`.
        latency_ms: Wall-clock latency in milliseconds of the call that produced `raw_text`.

    Returns:
        An `LLMResult` wrapping the validated model and the call's metadata.

    Raises:
        StructuredOutputError: `raw_text` fails to validate as `response_model`; carries
            `raw_text`, the validation error, and the same usage/cost/latency for diagnosis and
            billing of the failed call.
    """
    try:
        parsed = response_model.model_validate_json(raw_text)
    except ValidationError as err:
        raise StructuredOutputError(
            f"{response_model.__name__} failed validation",
            raw_text=raw_text,
            validation_error=str(err),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        ) from err
    return LLMResult(
        parsed=parsed,
        raw_text=raw_text,
        model=model,
        usage=usage,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def compute_cost_usd(price: ModelPrice, usage: LLMUsage) -> Decimal:
    """Compute the USD cost of `usage` at `price`, quantized to 6 decimal places.

    Uses `Decimal` throughout (never `float`) to avoid precision drift in billing math
    (CONVENTIONS.md §7).

    Args:
        price: Per-million-token USD prices for the model that produced `usage`.
        usage: Token counts to price.

    Returns:
        The cost in USD, quantized to `Decimal("0.000001")` with half-up rounding.
    """
    raw_cost = (
        Decimal(usage.input_tokens) * price.input_per_mtok
        + Decimal(usage.output_tokens) * price.output_per_mtok
    ) / Decimal(1_000_000)
    return raw_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
