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

Two-tier routing (m5 task-03, PRD §6.4): once the cheap pass above produces a validated verdict,
`worker.routing.should_escalate` decides (purely, no I/O) whether the SAME conversation escalates
to `strong_model` — iff `severity >= escalate_severity_gte` OR `confidence < escalate_confidence_lt`
and `strong_model` is configured. The strong pass is one more tool-less `_final` call over the
cheap conversation exactly as it stands (never the cheap verdict text itself, so the strong model
is never anchored on the cheap answer); it keeps the one PRD §6.5 validation retry and offers no
tools. Tokens/cost/latency keep accumulating across both tiers; the tool trace stays the cheap
pass's (the strong pass adds none). A strong-tier failure (`VerdictValidationError` or
`LLMCallError`) fails the attempt like any other failure — there is no fallback to the cheap
verdict, since `escalated_model=True` with the cheap verdict would misrecord the decision.

`triage_attempt` loads an alert, runs it through the pipeline, and persists the outcome — verdict,
tool-call trace, and status — as one transaction (PRD §6.2): success commits `persist_verdict`'s
write and returns an `AttemptResult`; any failure rolls that write back and RAISES (never
returning a status itself) — deciding retry-vs-fail from there is `worker/jobs.py`'s job (m5
task-02, with `worker/retry.py`), not the attempt's. `triage_alert` is `triage_attempt` plus the
M2 terminal write, unchanged: a validation or LLM-call failure marks the alert `failed` in its own
transaction instead of propagating.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.cache import TTLCache
from core.config import Settings
from core.errors import ConfigError, LLMCallError, StructuredOutputError, VerdictValidationError
from core.llm import ChatMessage, LLMClient, LLMResult, tool_calls_message
from core.models.alerts import AlertStatus
from core.schemas.alert import SessionAlert
from core.schemas.verdict import VERDICT_JSON_SCHEMA, Verdict
from core.services.alerts import get_alert, set_alert_status
from worker.outcome import ToolCallRecord, TriageOutcome
from worker.prompts import build_messages, build_tool_result_message, load_prompt
from worker.routing import should_escalate
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


@dataclass(frozen=True)
class AttemptResult:
    """One `triage_attempt` outcome: a committed verdict, or (task-04) a skip.

    `"skipped"` is minted by task-04 (a FOR UPDATE skip on a concurrently-claimed alert);
    declared now so the type never changes shape underneath `worker/jobs.py`.
    """

    status: Literal["triaged", "skipped"]
    verdict_id: uuid.UUID | None
    outcome: TriageOutcome | None


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
        strong_model: str | None = None,
        escalate_severity_gte: int | None = None,
        escalate_confidence_lt: float | None = None,
    ) -> None:
        """Load the prompt template once so a missing/malformed version fails at construction.

        Args:
            llm: The LLM client to call (real or `FakeLLMClient`).
            model: The model id to request completions from (the cheap tier).
            prompt_version: The prompt version to load (e.g. `"triage-v1"`).
            tools: The enrichment-tool registry to offer the model (PRD §6.3); `None` (default)
                runs the M3 tool-less pipeline unconditionally.
            tool_loop_max_iter: The hard cap on tool-call turns before a verdict is forced.
                Required (and must be `>= 1`) whenever `tools` is given.
            strong_model: The strong-tier model id (PRD §6.4, m5 task-03); `None` (default) keeps
                routing off — `run` never escalates. When given, both `escalate_severity_gte` and
                `escalate_confidence_lt` are required, and `strong_model` must differ from
                `model`.
            escalate_severity_gte: Escalate when the cheap verdict's severity is at least this.
                Required together with `escalate_confidence_lt` whenever `strong_model` is given;
                otherwise ignored.
            escalate_confidence_lt: Escalate when the cheap verdict's confidence is strictly
                below this. Required together with `escalate_severity_gte` whenever
                `strong_model` is given; otherwise ignored.

        Raises:
            ValueError: `tools` is given with `tool_loop_max_iter` `None` or `< 1`; or
                `strong_model` is given without both thresholds; or `strong_model == model`.
        """
        if tools is not None and (tool_loop_max_iter is None or tool_loop_max_iter < 1):
            raise ValueError("tool_loop_max_iter must be >= 1 when tools are given")
        if strong_model is not None:
            if escalate_severity_gte is None or escalate_confidence_lt is None:
                raise ValueError("escalation thresholds are required with strong_model")
            if strong_model == model:
                raise ValueError("strong_model must differ from model")
        self._llm = llm
        self._model = model
        self._prompt_version = prompt_version
        self._template = load_prompt(prompt_version)
        self._tools = tools
        self._tool_loop_max_iter = tool_loop_max_iter
        self._strong_model = strong_model
        self._escalate_severity_gte = escalate_severity_gte
        self._escalate_confidence_lt = escalate_confidence_lt

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        llm: LLMClient,
        recorder: ToolRecorder | None = None,
        cache: TTLCache | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> TriagePipeline:
        """Build the five-tool pipeline (`worker.tools.wiring.build_registry`) from `Settings`.

        The one construction `worker.main` uses at startup (m5 task-01: `api/` no longer builds
        a pipeline at all).

        Args:
            settings: The config surface to build the model, prompt version, registry and cap
                from.
            llm: The LLM client to call.
            recorder: How tools are actually executed; defaults to `LiveToolRecorder()` — always
                live, matching `worker.main`'s own use case.
            cache: An external `TTLCache` seam for `lookup_ip_reputation`; `None` lets
                `build_registry` build an in-process one sized from `settings`.
            http: The process-lifetime `httpx.AsyncClient` `lookup_ip_reputation` issues its
                request through (M4 task-06 fix-1 I4: the caller owns `aclose()` —
                `worker/main.py::shutdown` is the owner); `None` lets `build_registry` build one
                timed from `settings`.

        Returns:
            A `TriagePipeline` wired with every PRD §6.3 tool.

        Raises:
            ConfigError: `settings.strong_model` equals `settings.cheap_model` (a strong tier
                that answers exactly like the cheap tier would misrecord every escalation).
        """
        strong = settings.strong_model or None
        if strong == settings.cheap_model:
            raise ConfigError("STRONG_MODEL must differ from CHEAP_MODEL")
        return cls(
            llm=llm,
            model=settings.cheap_model,
            prompt_version=settings.triage_prompt_version,
            tools=build_registry(
                settings, recorder=recorder or LiveToolRecorder(), cache=cache, http=http
            ),
            tool_loop_max_iter=settings.tool_loop_max_iter,
            strong_model=strong,
            escalate_severity_gte=settings.escalate_severity_gte,
            escalate_confidence_lt=settings.escalate_confidence_lt,
        )

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Registered tool names, or `()` when this pipeline has no registry."""
        return self._tools.names if self._tools is not None else ()

    @property
    def strong_model(self) -> str | None:
        """The strong-tier model id, or `None` when routing is off (m5 task-03)."""
        return self._strong_model

    async def run(
        self,
        alert: SessionAlert,
        *,
        session: AsyncSession | None = None,
        now: datetime | None = None,
    ) -> TriageOutcome:
        """Summarize `alert`, run the tool loop (if any), validate the final reply, and — when
        `strong_model` is configured and `worker.routing.should_escalate` says so — escalate the
        SAME conversation to the strong tier once, tool-less (PRD §6.4, m5 task-03).

        Args:
            alert: The session alert to triage.
            session: The triage transaction's session, passed into `ToolContext` for DB-touching
                tools (`get_alert_history`); `None` outside `triage_alert` (the CLI, evals).
            now: The clock to inject into `ToolContext`; `None` builds an aware-UTC "now" (PRD
                §6.3: windows and caches must be deterministic in tests — R10).

        Returns:
            The `TriageOutcome` for the (possibly looped, possibly retried, possibly escalated)
            run. `model_primary`/`escalated_model` are set only when routing actually escalated.

        Raises:
            VerdictValidationError: The reply failed validation on both attempts of the one
                PRD §6.5 retry — on either tier. A strong-tier failure fails the attempt like any
                other; there is no fallback to the cheap verdict.
            LLMCallError: The underlying LLM call failed (either tier); propagates unretried.
        """
        ctx = ToolContext(alert=alert, session=session, now=now or datetime.now(UTC))
        summary = summarize_session(alert)
        messages = build_messages(self._template, summary=summary, schema=VERDICT_JSON_SCHEMA)

        records: list[ToolCallRecord] = []
        input_tokens = 0
        output_tokens = 0
        cost_usd = Decimal("0")
        latency_ms = 0

        def _outcome(result: LLMResult[Verdict], *, model: str, retried: bool) -> TriageOutcome:
            """Add `result`'s own usage/cost/latency to the running totals and build the outcome.

            `model` is parameterised (m5 task-03) so the same closure serves both the cheap and
            the strong pass while still sharing the one set of running totals.
            """
            return TriageOutcome(
                verdict=result.parsed,
                model=model,
                prompt_version=self._prompt_version,
                input_tokens=input_tokens + result.usage.input_tokens,
                output_tokens=output_tokens + result.usage.output_tokens,
                cost_usd=cost_usd + result.cost_usd,
                latency_ms=latency_ms + result.latency_ms,
                retried=retried,
                tool_calls=tuple(records),
            )

        async def _retry_once(
            msgs: list[ChatMessage], first_err: StructuredOutputError, *, model: str
        ) -> TriageOutcome:
            """The one PRD §6.5 retry: always tool-less, always the final word for this call's
            tier (`model` — the cheap or the strong id, m5 task-03)."""
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
                    messages=retry_messages, response_model=Verdict, model=model
                )
            except StructuredOutputError as second_err:
                raise VerdictValidationError(
                    "verdict failed validation twice",
                    attempts=2,
                    last_error=second_err.validation_error,
                ) from second_err
            return _outcome(result, model=model, retried=True)

        async def _final(msgs: list[ChatMessage], *, model: str) -> TriageOutcome:
            """The tool-less tail shared by the no-tools path, the cap-reached path, and the
            strong pass (m5 task-03): one `complete_structured` call, retried once on
            `StructuredOutputError` (PRD §6.5).
            """
            try:
                result = await self._llm.complete_structured(
                    messages=msgs, response_model=Verdict, model=model
                )
            except StructuredOutputError as first_err:
                return await _retry_once(msgs, first_err, model=model)
            return _outcome(result, model=model, retried=False)

        async def _cheap_pass() -> TriageOutcome:
            """The M4 tool loop, unchanged in behaviour, always at `self._model` (the cheap
            tier)."""
            nonlocal input_tokens, output_tokens, cost_usd, latency_ms
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
                        return await _retry_once(messages, first_err, model=self._model)

                    if isinstance(reply, LLMResult):
                        return _outcome(reply, model=self._model, retried=False)

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
                return await _final(messages, model=self._model)

            # No tools (or an empty registry): the M3 tool-less pipeline, byte for byte.
            return await _final(messages, model=self._model)

        cheap = await _cheap_pass()

        if self._strong_model is None:
            return cheap

        assert self._escalate_severity_gte is not None
        assert self._escalate_confidence_lt is not None
        decision = should_escalate(
            cheap.verdict,
            severity_gte=self._escalate_severity_gte,
            confidence_lt=self._escalate_confidence_lt,
        )
        if not decision.escalate:
            return cheap

        logger.info(
            "routing escalated reason=%s cheap_severity=%d cheap_confidence=%.2f cheap=%s "
            "strong=%s",
            decision.reason,
            cheap.verdict.severity,
            cheap.verdict.confidence,
            self._model,
            self._strong_model,
        )
        # Seed the running totals with the cheap pass's own final tallies (its own `_outcome`
        # call folded its result's usage into its return value, not back into these nonlocals) so
        # `_final`'s strong-tier accumulation sums across BOTH tiers, not the strong call alone.
        input_tokens = cheap.input_tokens
        output_tokens = cheap.output_tokens
        cost_usd = cheap.cost_usd
        latency_ms = cheap.latency_ms
        # `messages` is the cheap conversation exactly as it stands: system prompt, delimited
        # summary, every tool-call turn/result, and FINAL_VERDICT_INSTRUCTION on the cap path —
        # the cheap verdict text itself is never appended (no anchoring), and no tools are
        # offered.
        strong = await _final(messages, model=self._strong_model)
        return replace(
            strong,
            model=self._strong_model,
            model_primary=self._model,
            escalated_model=True,
            retried=cheap.retried or strong.retried,
            tool_calls=cheap.tool_calls,
        )

    async def triage_attempt(self, session: AsyncSession, alert_id: uuid.UUID) -> AttemptResult:
        """Load `alert_id`, run it through the pipeline, and persist the outcome as one unit.

        Success persists the verdict and its tool-call trace (`worker.store.persist_verdict`) and
        commits once, per PRD §6.2. ANY failure (including cancellation) rolls that write back and
        RAISES unchanged — deciding retry-vs-fail is `worker/jobs.py`'s job (m5 task-02), not this
        method's. The alert is loaded and `run` completes fully before any write, so a tool's own
        SAVEPOINT (`get_alert_history`'s `session.begin_nested()`) never autoflushes pending ORM
        state ahead of `persist_verdict`'s own writes.

        Args:
            session: The request/job-scoped `AsyncSession`; this method owns its commit.
            alert_id: The alert to triage.

        Returns:
            `AttemptResult(status="triaged", verdict_id=<the new row's id>, outcome=<the run's
            TriageOutcome>)` on success.

        Raises:
            NotFoundError: `alert_id` does not exist (propagates from `get_alert`).
            VerdictValidationError | LLMCallError: The run failed; the session was rolled back
                first.
        """
        row = await get_alert(session, alert_id)
        alert = SessionAlert.model_validate(row.raw)
        try:
            outcome = await self.run(alert, session=session)
            verdict_id = await persist_verdict(
                session,
                alert_id=alert_id,
                outcome=outcome,
                model_primary=outcome.model_primary or outcome.model,
                escalated_model=outcome.escalated_model,
                tool_calls=outcome.tool_calls,
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        return AttemptResult(status="triaged", verdict_id=verdict_id, outcome=outcome)

    async def triage_alert(self, session: AsyncSession, alert_id: uuid.UUID) -> AlertStatus:
        """Load `alert_id`, run it through the pipeline, and persist the outcome as one unit.

        Unchanged M2 contract, now expressed over `triage_attempt`: success returns "triaged"
        (a `"skipped"` `AttemptResult`, minted by task-04, is also reported as "triaged" here —
        its alert already IS triaged). A validation or LLM-call failure marks the alert `failed`
        in its own transaction instead — never a 5xx, never a half-written verdict.

        Args:
            session: The request/job-scoped `AsyncSession`; this method owns its commit(s).
            alert_id: The alert to triage.

        Returns:
            `"triaged"` on success, `"failed"` on a validation or LLM-call failure.

        Raises:
            NotFoundError: `alert_id` does not exist (propagates from `get_alert`).
        """
        try:
            await self.triage_attempt(session, alert_id)
            return "triaged"
        except (VerdictValidationError, LLMCallError) as exc:
            await set_alert_status(session, alert_id, "failed")
            await session.commit()
            logger.warning("triage failed alert_id=%s code=%s", alert_id, exc.code)
            return "failed"
