"""`TriagePipeline`: prompt → LLM → (tools, PRD §6.3) → validate → retry once (PRD §6.5, §6.2).

Without a `tools=` registry (or one with no registered tools), `run` is byte-for-byte the M3
`complete_structured` pipeline. With one, `run` alternates `complete_with_tools` turns: the model
may ask for tools, which are executed through the registry (never raising — an unknown tool, a
raising tool, and a tool's own `unavailable(...)` answer all flow back to the model, spine
constraint M4-a) and fed back **inside** the `<<<ALERT_DATA>>>` markers
(`worker.prompts.build_tool_result_message`, PRD §10.6: tool results are attacker data, delimited
like the summary, and cannot forge the markers). This repeats for at most `tool_loop_max_iter`
tool turns (`core.config.Settings.tool_loop_max_iter`, never a literal — CONVENTIONS.md §7); if
the cap is reached without a content reply, one final tool-less call is forced with
`FINAL_VERDICT_INSTRUCTION` appended as the last message.

The one PRD §6.5 structured-output retry is preserved from M3 and always tool-less: on a
`StructuredOutputError` (from either a tool-less call or a `complete_with_tools` content reply
that failed validation), the failed reply and the validation error are appended to the
conversation and the call is retried exactly once via `complete_structured`; a second
`StructuredOutputError` raises `VerdictValidationError(attempts=2, ...)` instead of retrying
again. `LLMCallError` (transport/HTTP failure) is never retried here — job-level retries (M5) own
that (`.claude/rules/worker.md`). Tokens, cost and LLM latency are summed across every LLM turn —
tool turns, the forced final, and the retry — but never across tool execution time, which is
recorded per row in `TriageOutcome.tool_calls` instead (`verdicts.latency_ms` keeps its M0
meaning: LLM time only).

`triage_alert` loads an alert, runs it through the pipeline, and persists the outcome — verdict,
tool-call trace, and status — as one transaction (PRD §6.2): success commits `persist_verdict`'s
write; a validation or LLM-call failure rolls that write back and marks the alert `failed` in its
own transaction instead.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.cache import TTLCache
from core.config import Settings
from core.errors import LLMCallError, StructuredOutputError, VerdictValidationError
from core.llm import ChatMessage, LLMClient, LLMResult, tool_calls_message
from core.models.alerts import AlertStatus
from core.schemas.alert import SessionAlert
from core.schemas.verdict import VERDICT_JSON_SCHEMA, Verdict
from core.services.alerts import get_alert, set_alert_status
from worker.outcome import ToolCallRecord, TriageOutcome
from worker.prompts import build_messages, build_tool_result_message, load_prompt
from worker.store import persist_verdict
from worker.summarize import summarize_session
from worker.tools import LiveToolRecorder, ToolContext, ToolRecorder, ToolRegistry
from worker.tools.wiring import build_registry

logger = logging.getLogger(__name__)

RETRY_INSTRUCTION = (
    "Your previous reply failed validation: {error}\n"
    "Reply with ONLY a JSON object that matches the schema in the system message."
)

FINAL_VERDICT_INSTRUCTION = (
    "The tool budget is exhausted. Using only the evidence already gathered, reply with ONLY a "
    "JSON object that matches the schema in the system message."
)


class TriagePipeline:
    """Runs one alert through a single prompt version and model, optionally with tools."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        model: str,
        prompt_version: str,
        tools: ToolRegistry | None = None,
        tool_loop_max_iter: int | None = None,
    ) -> None:
        """Load the prompt template once so a missing/malformed version fails at construction.

        Args:
            llm: The LLM client to call (real or `FakeLLMClient`).
            model: The model id to request completions from.
            prompt_version: The prompt version to load (e.g. `"triage-v1"`).
            tools: The enrichment-tool registry to offer the model (PRD §6.3); `None` (default)
                runs the M3 tool-less pipeline unconditionally.
            tool_loop_max_iter: The hard cap on tool-call turns before a verdict is forced.
                Required (and must be `>= 1`) whenever `tools` is given.

        Raises:
            ValueError: `tools` is given with `tool_loop_max_iter` `None` or `< 1`.
        """
        if tools is not None and (tool_loop_max_iter is None or tool_loop_max_iter < 1):
            raise ValueError("tool_loop_max_iter must be >= 1 when tools are given")
        self._llm = llm
        self._model = model
        self._prompt_version = prompt_version
        self._template = load_prompt(prompt_version)
        self._tools = tools
        self._tool_loop_max_iter = tool_loop_max_iter

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        llm: LLMClient,
        recorder: ToolRecorder | None = None,
        cache: TTLCache | None = None,
    ) -> TriagePipeline:
        """Build the five-tool pipeline (`worker.tools.wiring.build_registry`) from `Settings`.

        The one construction `api.main` uses, so `api.main` needs no new `worker` import beyond
        this module (import-linter contract 3's M2-only ignore list is unchanged).

        Args:
            settings: The config surface to build the model, prompt version, registry and cap
                from.
            llm: The LLM client to call.
            recorder: How tools are actually executed; defaults to `LiveToolRecorder()` — always
                live, matching `api.main`'s own use case.
            cache: An external `TTLCache` seam for `lookup_ip_reputation`; `None` lets
                `build_registry` build an in-process one sized from `settings`.

        Returns:
            A `TriagePipeline` wired with every PRD §6.3 tool.
        """
        return cls(
            llm=llm,
            model=settings.cheap_model,
            prompt_version=settings.triage_prompt_version,
            tools=build_registry(settings, recorder=recorder or LiveToolRecorder(), cache=cache),
            tool_loop_max_iter=settings.tool_loop_max_iter,
        )

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Registered tool names, or `()` when this pipeline has no registry."""
        return self._tools.names if self._tools is not None else ()

    async def run(
        self,
        alert: SessionAlert,
        *,
        session: AsyncSession | None = None,
        now: datetime | None = None,
    ) -> TriageOutcome:
        """Summarize `alert`, run the tool loop (if any), and validate the final reply.

        Args:
            alert: The session alert to triage.
            session: The triage transaction's session, passed into `ToolContext` for DB-touching
                tools (`get_alert_history`); `None` outside `triage_alert` (the CLI, evals).
            now: The clock to inject into `ToolContext`; `None` builds an aware-UTC "now" (PRD
                §6.3: windows and caches must be deterministic in tests — R10).

        Returns:
            The `TriageOutcome` for the (possibly looped, possibly retried) run.

        Raises:
            VerdictValidationError: The reply failed validation on both attempts of the one
                PRD §6.5 retry.
            LLMCallError: The underlying LLM call failed; propagates unretried.
        """
        ctx = ToolContext(alert=alert, session=session, now=now or datetime.now(UTC))
        summary = summarize_session(alert)
        messages = build_messages(self._template, summary=summary, schema=VERDICT_JSON_SCHEMA)

        records: list[ToolCallRecord] = []
        input_tokens = 0
        output_tokens = 0
        cost_usd = Decimal("0")
        latency_ms = 0

        def _outcome(result: LLMResult[Verdict], *, retried: bool) -> TriageOutcome:
            """Add `result`'s own usage/cost/latency to the running totals and build the outcome."""
            return TriageOutcome(
                verdict=result.parsed,
                model=self._model,
                prompt_version=self._prompt_version,
                input_tokens=input_tokens + result.usage.input_tokens,
                output_tokens=output_tokens + result.usage.output_tokens,
                cost_usd=cost_usd + result.cost_usd,
                latency_ms=latency_ms + result.latency_ms,
                retried=retried,
                tool_calls=tuple(records),
            )

        async def _retry_once(
            msgs: list[ChatMessage], first_err: StructuredOutputError
        ) -> TriageOutcome:
            """The one PRD §6.5 retry: always tool-less, always the final word for this run."""
            nonlocal input_tokens, output_tokens, cost_usd, latency_ms
            input_tokens += first_err.input_tokens
            output_tokens += first_err.output_tokens
            cost_usd += first_err.cost_usd
            latency_ms += first_err.latency_ms
            retry_messages: list[ChatMessage] = [
                *msgs,
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
            return _outcome(result, retried=True)

        async def _final(msgs: list[ChatMessage]) -> TriageOutcome:
            """The tool-less tail shared by the no-tools path and the cap-reached path: one
            `complete_structured` call, retried once on `StructuredOutputError` (PRD §6.5).
            """
            try:
                result = await self._llm.complete_structured(
                    messages=msgs, response_model=Verdict, model=self._model
                )
            except StructuredOutputError as first_err:
                return await _retry_once(msgs, first_err)
            return _outcome(result, retried=False)

        specs = self._tools.specs() if self._tools is not None else []
        has_tools = self._tools is not None and bool(specs)
        if has_tools:
            assert self._tools is not None and self._tool_loop_max_iter is not None
            seq = 0
            for _turn in range(self._tool_loop_max_iter):
                try:
                    reply = await self._llm.complete_with_tools(
                        messages=messages,
                        tools=specs,
                        response_model=Verdict,
                        model=self._model,
                    )
                except StructuredOutputError as first_err:
                    return await _retry_once(messages, first_err)

                if isinstance(reply, LLMResult):
                    return _outcome(reply, retried=False)

                # ToolCallTurn: the model asked for one or more tools this turn.
                input_tokens += reply.usage.input_tokens
                output_tokens += reply.usage.output_tokens
                cost_usd += reply.cost_usd
                latency_ms += reply.latency_ms
                messages.append(tool_calls_message(reply.calls))
                for call in reply.calls:
                    execution = await self._tools.execute(call.name, call.arguments, ctx)
                    records.append(
                        ToolCallRecord(
                            seq=seq,
                            tool_name=call.name,
                            arguments=dict(call.arguments),
                            result=execution.result,
                            latency_ms=execution.latency_ms,
                        )
                    )
                    seq += 1
                    messages.append(build_tool_result_message(call.id, execution.result))

            # Cap reached without a content reply: force one tool-less final call.
            messages.append({"role": "user", "content": FINAL_VERDICT_INSTRUCTION})
            return await _final(messages)

        # No tools (or an empty registry): the M3 tool-less pipeline, byte for byte.
        return await _final(messages)

    async def triage_alert(self, session: AsyncSession, alert_id: uuid.UUID) -> AlertStatus:
        """Load `alert_id`, run it through the pipeline, and persist the outcome as one unit.

        Success persists the verdict and its tool-call trace (`worker.store.persist_verdict`) and
        commits once, per PRD §6.2. A validation or LLM-call failure rolls that write back and
        marks the alert `failed` in its own transaction instead — never a 5xx, never a
        half-written verdict. The alert is loaded and `run` completes fully before any write, so a
        tool's own SAVEPOINT (`get_alert_history`'s `session.begin_nested()`) never autoflushes
        pending ORM state ahead of `persist_verdict`'s own writes.

        Args:
            session: The request/job-scoped `AsyncSession`; this method owns its commit(s).
            alert_id: The alert to triage.

        Returns:
            `"triaged"` on success, `"failed"` on a validation or LLM-call failure.

        Raises:
            NotFoundError: `alert_id` does not exist (propagates from `get_alert`).
        """
        row = await get_alert(session, alert_id)
        alert = SessionAlert.model_validate(row.raw)
        try:
            outcome = await self.run(alert, session=session)
            await persist_verdict(
                session,
                alert_id=alert_id,
                outcome=outcome,
                model_primary=self._model,
                tool_calls=outcome.tool_calls,
            )
            await session.commit()
            return "triaged"
        except (VerdictValidationError, LLMCallError) as exc:
            await session.rollback()
            await set_alert_status(session, alert_id, "failed")
            await session.commit()
            logger.warning("triage failed alert_id=%s code=%s", alert_id, exc.code)
            return "failed"
