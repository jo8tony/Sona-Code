"""Application-owned conversation scope and native V1 chat configuration."""

from __future__ import annotations

import json

CHAT_SCOPE_ID = "chat"
CHAT_AGENT = "sonacode-chat"
CHAT_PROMPT = (
    "You are Sona, a helpful conversational assistant. Answer questions, explain concepts, "
    "help with writing, and discuss ideas and code. Respond in the user's language. "
    "This conversation has no user project or filesystem access. Do not claim to inspect "
    "files, run commands, search the web, or perform actions. Ask for relevant content "
    "in the conversation when needed."
)


def chat_environment(environment: dict[str, str]) -> dict[str, str]:
    """Preserve compiled providers while enforcing a tool-free primary agent."""
    env = dict(environment)
    inline = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
    inline["default_agent"] = CHAT_AGENT
    inline["permission"] = {"*": "deny"}
    agents = inline.setdefault("agent", {})
    agents[CHAT_AGENT] = {
        "description": "普通对话", "mode": "primary", "prompt": CHAT_PROMPT,
        "permission": {"*": "deny"},
    }
    # V1 also uses this flag for ancestor AGENTS.md discovery. A chat scope
    # must not inherit the repository surrounding a development config file.
    env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
    env["OPENCODE_DISABLE_CLAUDE_CODE"] = "1"
    env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "1"
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline, ensure_ascii=False)
    return env
