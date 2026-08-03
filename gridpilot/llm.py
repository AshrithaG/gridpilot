"""LLM providers.

Two backends, same interface. `anthropic` uses the SDK and an API key.
`claude_cli` shells out to the locally authenticated `claude` binary, which
means the project runs for anyone with Claude Code installed and no key to
manage -- handy for a demo, and it keeps the eval reproducible on a laptop.

Tool calling is expressed as JSON in the response text rather than the
provider's native tool API, so both backends behave identically and a
transcript captured from one can be replayed against the other.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Reply:
    text: str
    tool: str | None = None
    args: dict = field(default_factory=dict)
    raw: str = ""


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_tool_call(text: str) -> Reply:
    """Pull the tool call out of a response. Accepts a bare object, a fenced
    block, or prose with an object at the end -- models drift between these and
    a strict parser would fail for the wrong reason."""
    candidates: list[str] = []
    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1))
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    # last balanced object in the text
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:i + 1])
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "tool" in obj:
            args = obj.get("args") or obj.get("input") or {}
            if not isinstance(args, dict):
                args = {}
            return Reply(text=text, tool=str(obj["tool"]), args=args, raw=cand)
    return Reply(text=text, tool=None, args={}, raw="")


class ClaudeCLI:
    """Talks to the `claude` binary in headless mode."""

    kind = "claude_cli"

    def __init__(self, model: str = DEFAULT_MODEL, timeout: int = 180):
        self.model = model
        self.timeout = timeout
        self.usage = Usage()

    def complete(self, system: str, messages: list[dict]) -> Reply:
        convo = [f"[{m['role']}]\n{m['content']}" for m in messages]
        prompt = f"{system}\n\n" + "\n\n".join(convo) + \
                 "\n\n[assistant]\nRespond with exactly one JSON object and nothing else."
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", self.model],
            capture_output=True, text=True, timeout=self.timeout,
            cwd=os.path.expanduser("~"),
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude cli failed: {proc.stderr.strip()[:300]}")
        self.usage.calls += 1
        # the CLI does not report tokens in plain mode; approximate for cost tracking
        self.usage.input_tokens += len(prompt) // 4
        self.usage.output_tokens += len(proc.stdout) // 4
        return parse_tool_call(proc.stdout)


class AnthropicAPI:
    kind = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 1024):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.usage = Usage()

    def complete(self, system: str, messages: list[dict]) -> Reply:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        self.usage.calls += 1
        self.usage.input_tokens += resp.usage.input_tokens
        self.usage.output_tokens += resp.usage.output_tokens
        text = "".join(b.text for b in resp.content if b.type == "text")
        return parse_tool_call(text)


class Scripted:
    """Fixed replies, for tests and for replaying a recorded run."""

    kind = "scripted"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.usage = Usage()

    def complete(self, system: str, messages: list[dict]) -> Reply:
        self.usage.calls += 1
        text = self.replies.pop(0) if self.replies else '{"tool":"done","args":{}}'
        return parse_tool_call(text)


def get_provider(name: str = "auto", model: str = DEFAULT_MODEL):
    if name == "auto":
        name = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "claude_cli"
    if name == "anthropic":
        return AnthropicAPI(model=model)
    if name == "claude_cli":
        return ClaudeCLI(model=model)
    raise ValueError(f"unknown provider {name}")
