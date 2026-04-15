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

from openharness.engine.messages import ConversationMessage
from openharness.engine.query import QueryContext, TurnResult, run_single_turn
from openharness.engine.stream_events import (
    AssistantTurnComplete,
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
        _track_usage: Callable[[Any], None] | None = None,
        _log_event: Callable[[Any], None] | None = None,
        _log_messages: Callable[[list[ConversationMessage]], None] | None = None,
    ) -> None:
        self._query_ctx = query_ctx
        self._messages = messages
        self._track_usage = _track_usage or (lambda u: None)
        self._log_event = _log_event or (lambda e: None)
        self._log_messages = _log_messages or (lambda m: None)

        self._final_text = ""
        self._is_complete = False
        self._logged_up_to = 0

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
        result = await run_single_turn(self._query_ctx, self._messages)

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
    ) -> str:
        """Run the agentic loop until done.

        Args:
            on_turn_complete: Optional async callback fired after each turn.
                The callback receives the ``TurnResult`` and this
                ``Conversation`` instance — it can call ``inject()`` to
                add messages or set ``_is_complete`` to force-stop.

        Can be called again after ``inject()`` to continue the conversation.
        """
        self._final_text = ""
        self._is_complete = False

        try:
            while not self._is_complete:
                result = await self.step()
                if on_turn_complete is not None:
                    await on_turn_complete(result, self)
        finally:
            new_messages = self._messages[self._logged_up_to :]
            self._log_messages(new_messages)
            self._logged_up_to = len(self._messages)

        return self._final_text
