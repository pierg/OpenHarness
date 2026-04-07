import os
from openharness.tools.send_message_tool import _resolve_sender_agent_id

print("No env var:", _resolve_sender_agent_id())
os.environ["CLAUDE_CODE_AGENT_ID"] = "coordinator"
print("With env var:", _resolve_sender_agent_id())
