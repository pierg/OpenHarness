"""Conversation — a controllable handle over an agent's multi-turn LLM loop.

Lives in ``engine/`` because it depends only on engine primitives
(``QueryContext``, ``run_single_turn``, stream events).  The *runtime*
layer creates ``Conversation`` instances and wires in usage tracking /
logging callbacks.

Two consumption models:

- **step()** — execute one turn, get a ``TurnResult``, make decisions.
- **run_to_completion()** — loop ``step()`` until done, with an optional
  ``on_turn_complete`` callback between turns.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from openharness.engine.loop_guard import LoopGuardState, inspect_turn
from openharness.engine.messages import ConversationMessage
from openharness.engine.query import QueryContext, TurnResult, run_single_turn
from openharness.engine.stream_events import (
    AssistantTurnComplete,
    ModelRequest,
    StatusEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)

OnTurnComplete = Callable[["TurnResult", "Conversation"], Awaitable[None]]


class Conversation:
    """Controllable handle over a single agent's multi-turn conversation."""

    def __init__(
        self,
        query_ctx: QueryContext,
        messages: list[ConversationMessage],
        *,
        agent_name: str | None = None,
        _track_usage: Callable[[Any], None] | None = None,
        _log_event: Callable[[Any], None] | None = None,
        _log_messages: Callable[[list[ConversationMessage]], None] | None = None,
    ) -> None:
        self._query_ctx = query_ctx
        self._messages = messages
        self._agent_name = agent_name
        self._track_usage = _track_usage or (lambda u: None)
        self._log_event = _log_event or (lambda e: None)
        self._log_messages = _log_messages or (lambda m: None)

        self._final_text = ""
        self._is_complete = False
        self._logged_up_to = 0
        self._turns_taken = 0

    # ------------------------------------------------------------------
    # Read-only state
    # ------------------------------------------------------------------

    @property
    def messages(self) -> list[ConversationMessage]:
        return self._messages

    @property
    def final_text(self) -> str:
        return self._final_text

    @property
    def is_complete(self) -> bool:
        return self._is_complete

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def inject(self, message: ConversationMessage) -> None:
        """Insert a message and re-open the conversation for another round."""
        self._messages.append(message)
        self._is_complete = False

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def step(self) -> TurnResult:
        """Execute one LLM turn (API call + tool execution).

        Returns a ``TurnResult`` describing what happened.  The caller
        can inspect it, ``inject()`` messages, or let the loop continue.
        """
        # Emit a request-side audit event before each model call so
        # downstream tooling can see exactly what crossed to the
        # provider for this turn (system prompt, tool surface, request
        # parameters). Pairs with the AssistantTurnComplete event that
        # follows. See engine.stream_events.ModelRequest.
        self._log_event(
            ModelRequest(
                model=self._query_ctx.model,
                system_prompt=self._query_ctx.system_prompt,
                tools=tuple(
                    schema.get("name", "")
                    for schema in self._query_ctx.tool_registry.to_api_schema()
                ),
                max_tokens=self._query_ctx.max_tokens,
                max_turns=self._query_ctx.max_turns,
                turn_index=self._turns_taken + 1,
                message_count=len(self._messages),
                agent=self._agent_name,
            )
        )

        result = await run_single_turn(self._query_ctx, self._messages)
        self._turns_taken += 1

        self._track_usage(result.usage)
        self._log_event(AssistantTurnComplete(message=result.message, usage=result.usage))
        for tc in result.tool_calls:
            self._log_event(ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input))
        for tc, tr in zip(result.tool_calls, result.tool_results):
            self._log_event(
                ToolExecutionCompleted(
                    tool_name=tc.name,
                    output=tr.content,
                    is_error=tr.is_error,
                )
            )

        self._final_text = result.text
        if not self._final_text and result.tool_results:
            # Fallback to last tool output if assistant provided no text
            self._final_text = result.tool_results[-1].content

        if result.is_final:
            self._is_complete = True

        return result

    async def run_to_completion(
        self,
        on_turn_complete: OnTurnComplete | None = None,
        *,
        loop_guard: LoopGuardState | None = None,
    ) -> str:
        """Run the agentic loop until done.

        Args:
            on_turn_complete: Optional async callback fired after each turn.
                The callback receives the ``TurnResult`` and this
                ``Conversation`` instance — it can call ``inject()`` to
                add messages or set ``_is_complete`` to force-stop.
            loop_guard: Optional loop-guard state. When provided, the guard
                inspects each completed turn and — if it detects an empty
                turn or an identical-tool-call loop — injects a short
                steering user message to nudge the model back on track.

        Can be called again after ``inject()`` to continue the conversation.

        When the underlying ``QueryContext`` has a ``max_turns`` budget
        and that budget is reached without the model finishing, the
        loop stops and returns whatever ``final_text`` was produced on
        the last completed turn (a ``StatusEvent`` records the budget
        exhaustion so the failure mode is visible).  This mirrors the
        graceful behaviour callers usually want from a runaway agent —
        the verifier can still score on-disk artefacts and a planner
        can still hand its best-effort plan to an executor.
        """
        self._final_text = ""
        self._is_complete = False
        max_turns = self._query_ctx.max_turns

        try:
            while not self._is_complete:
                if max_turns is not None and self._turns_taken >= max_turns:
                    self._log_event(
                        StatusEvent(
                            message=(
                                f"max_turns budget exhausted "
                                f"({self._turns_taken}/{max_turns}); "
                                "returning best-effort final_text"
                            )
                        )
                    )
                    self._is_complete = True
                    break
                result = await self.step()
                if on_turn_complete is not None:
                    await on_turn_complete(result, self)
                if loop_guard is not None:
                    nudge = inspect_turn(loop_guard, result)
                    if nudge is not None:
                        self._log_event(
                            StatusEvent(
                                message=(
                                    "loop_guard: injecting steering message "
                                    f"(recoveries={loop_guard.recoveries_used}/"
                                    f"{loop_guard.config.max_recoveries})"
                                )
                            )
                        )
                        self.inject(nudge)
        finally:
            new_messages = self._messages[self._logged_up_to :]
            self._log_messages(new_messages)
            self._logged_up_to = len(self._messages)

        return self._final_text
