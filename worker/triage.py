"""`TriagePipeline`: prompt → LLM → validate → retry once (PRD §6.5, §6.2).

One structured-output retry lives here: on `StructuredOutputError`, the failed reply and the
validation error are appended to the conversation and the call is retried exactly once; a second
`StructuredOutputError` raises `VerdictValidationError(attempts=2, ...)` instead of trying again.
`LLMCallError` (transport/HTTP failure) is never retried here — job-level retries (M5) own that
(`.claude/rules/worker.md`). Tokens, cost and latency are summed across both attempts, including a
failed attempt's usage, so billing is correct under retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.errors import StructuredOutputError, VerdictValidationError
from core.llm import ChatMessage, LLMClient
from core.schemas.alert import SessionAlert
from core.schemas.verdict import VERDICT_JSON_SCHEMA, Verdict
from worker.prompts import build_messages, load_prompt
from worker.summarize import summarize_session

RETRY_INSTRUCTION = (
    "Your previous reply failed validation: {error}\n"
    "Reply with ONLY a JSON object that matches the schema in the system message."
)


@dataclass(frozen=True)
class TriageOutcome:
    """One triage pipeline run's verdict plus billing/routing metadata."""

    verdict: Verdict
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    retried: bool


class TriagePipeline:
    """Runs one alert through a single prompt version and model, with one validation retry."""

    def __init__(self, *, llm: LLMClient, model: str, prompt_version: str) -> None:
        """Load the prompt template once so a missing/malformed version fails at construction.

        Args:
            llm: The LLM client to call (real or `FakeLLMClient`).
            model: The model id to request completions from.
            prompt_version: The prompt version to load (e.g. `"triage-v1"`).
        """
        self._llm = llm
        self._model = model
        self._prompt_version = prompt_version
        self._template = load_prompt(prompt_version)

    async def run(self, alert: SessionAlert) -> TriageOutcome:
        """Summarize `alert`, call the LLM, and validate the reply, retrying once if needed.

        Args:
            alert: The session alert to triage.

        Returns:
            The `TriageOutcome` for the (possibly retried) call.

        Raises:
            VerdictValidationError: The reply failed validation on both attempts.
            LLMCallError: The underlying LLM call failed; propagates unretried.
        """
        summary = summarize_session(alert)
        messages = build_messages(self._template, summary=summary, schema=VERDICT_JSON_SCHEMA)

        try:
            result = await self._llm.complete_structured(
                messages=messages, response_model=Verdict, model=self._model
            )
        except StructuredOutputError as first_err:
            retry_messages: list[ChatMessage] = [
                *messages,
                {"role": "assistant", "content": first_err.raw_text},
                {
                    "role": "user",
                    "content": RETRY_INSTRUCTION.format(error=first_err.validation_error),
                },
            ]
            try:
                result = await self._llm.complete_structured(
                    messages=retry_messages, response_model=Verdict, model=self._model
                )
            except StructuredOutputError as second_err:
                raise VerdictValidationError(
                    "verdict failed validation twice",
                    attempts=2,
                    last_error=second_err.validation_error,
                ) from second_err
            return TriageOutcome(
                verdict=result.parsed,
                model=self._model,
                prompt_version=self._prompt_version,
                input_tokens=first_err.input_tokens + result.usage.input_tokens,
                output_tokens=first_err.output_tokens + result.usage.output_tokens,
                cost_usd=first_err.cost_usd + result.cost_usd,
                latency_ms=first_err.latency_ms + result.latency_ms,
                retried=True,
            )

        return TriageOutcome(
            verdict=result.parsed,
            model=self._model,
            prompt_version=self._prompt_version,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            retried=False,
        )
