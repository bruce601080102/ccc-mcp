from .base import BaseAgentAdapter
from .claude import ClaudeCodeAdapter
from .codex import CodexAdapter
from .fake import FakeAgentAdapter

# CLI-dispatched providers only. "fake" is selected by adapter kind, not provider,
# and "chatgpt" is interactive (handled by prepare_chatgpt_execution), never dispatched
# through this registry, so neither belongs here.
ADAPTERS: dict[str, type[BaseAgentAdapter]] = {
    "codex": CodexAdapter,
    "claude": ClaudeCodeAdapter,
}

__all__ = ["ADAPTERS", "BaseAgentAdapter", "ClaudeCodeAdapter", "CodexAdapter", "FakeAgentAdapter"]
