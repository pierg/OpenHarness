"""Think tool.

A no-op tool that gives the model a sanctioned scratchpad slot in its
turn loop. Mirrors the "think" tool from Anthropic's tool-use research
(Mar 2025) and the same pattern used in modern SWE-bench / TB2 reference
agents. Recording the thought as a tool call makes the reasoning step
visible in trajectories and discourages the common failure mode where
weaker models repurpose ``bash`` as a comment-only scratchpad.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import Workspace


class ThinkToolInput(BaseModel):
    """Arguments for think."""

    thought: str = Field(
        ...,
        min_length=1,
        description=(
            "Free-form reasoning to record before the next action. Use this to "
            "plan, analyze a noisy tool result, verify rules from the task "
            "instructions, or sketch the next step. The thought is logged in "
            "the trajectory but has no side effects."
        ),
    )


class ThinkTool(BaseTool):
    """Record a private reasoning step. Has no side effects."""

    name = "think"
    description = (
        "Record a private reasoning step. Use BEFORE complex tool calls to "
        "plan, AFTER noisy tool results to interpret them, or to verify rules "
        "from the task instructions. Has no side effects — the thought is "
        "just echoed back to the trajectory. Do NOT use the bash tool to "
        "write comment-only commands as a scratchpad — use this tool instead."
    )
    input_model = ThinkToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        del workspace

    def is_read_only(self, arguments: ThinkToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: ThinkToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        return ToolResult(output=arguments.thought)
