"""The LLM operator.

Each tick the agent gets the grid state and a tool loop. The prompt does not
tell it which lever to pull; it tells it what the levers are, that shed load
counts as lost load, and that it can simulate before committing. Whether it
works out "redispatch first, shed only if that fails" is the thing being
measured.

The guardrail is not advice in the prompt -- it is enforced in code. Actions
are schema-validated, capped per tick, blocked on protected buses, and
optionally forced through a what-if check that rejects any plan worse than
doing nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from gridpilot.cascade import CascadeSim
from gridpilot.llm import Usage, get_provider
from gridpilot.sensitivity import relief_options
from gridpilot.tools import (
    MAX_ACTIONS_PER_TICK,
    PROTECTED_BUSES,
    dispatch,
    grid_state,
    what_if,
)

SYSTEM = f"""You are the operator on shift for a transmission grid. A fault has
occurred and lines may be overloaded. Your job is to keep as much load served
as possible.

Physics you need to know:
- A line above 100% of its thermal limit trips after a couple of ticks, or
  immediately above 140%. Every trip pushes its flow onto neighbouring lines,
  which is how one fault becomes a cascade.
- If the grid splits into islands, each island must balance generation against
  load on its own. An island short of generation sheds load automatically, and
  an island with no generation goes completely dark.
- Generators can only move within their spinning reserve on this timescale,
  roughly 10% above their current output.

Your levers:
- redispatch: move a generator to a new setpoint. This changes where power
  flows and can relieve an overloaded line WITHOUT dropping any customers.
- shed_load: curtail demand at a bus. This relieves flows but the shed load
  counts as lost load in your score, so it is a cost, not a free action.
- open_line / close_line: change the topology.

Scoring: you are judged on total load lost at the end of the incident,
including anything you shed yourself. Doing nothing is often better than
acting carelessly. Buses {sorted(PROTECTED_BUSES)} are critical and cannot be shed.

Available tools:
- get_grid_state: current overloads, islands, and sheddable load by bus.
- relief_options: for a given line, which generators relieve it and by how
  much per MW. Use this before redispatching; guessing wastes reserve.
- what_if: simulate a list of actions and see the outcome WITHOUT applying it.
  It reports what would be lost versus doing nothing. Use it to check a plan.
- apply_actions: commit actions to the real grid (at most {MAX_ACTIONS_PER_TICK} per tick).
- done: end your turn for this tick.

Action shapes, exactly as written (mw is always an absolute setpoint, never a delta):
  {{"type":"redispatch","gen":12,"mw":180.0}}
  {{"type":"shed_load","bus":42,"mw":25.0}}
  {{"type":"open_line","line":57}}

Reply with exactly one JSON object per turn and no other text:
{{"tool": "<tool name>", "args": {{...}}}}

For example:
  {{"tool":"relief_options","args":{{"line":57}}}}
  {{"tool":"what_if","args":{{"actions":[{{"type":"redispatch","gen":12,"mw":180.0}}]}}}}
  {{"tool":"apply_actions","args":{{"actions":[{{"type":"redispatch","gen":12,"mw":180.0}}],
   "reasoning":"raising gen 12 relieves line 57"}}}}
"""

TOOL_HINT = {
    "get_grid_state": "{}",
    "relief_options": '{"line": <int>}',
    "what_if": '{"actions": [{"type":"redispatch","gen":<int>,"mw":<float>}]}',
    "apply_actions": '{"actions": [...], "reasoning": "<why>"}',
    "done": '{"reasoning": "<why>"}',
}


@dataclass
class Turn:
    tick: int
    tool: str | None
    args: dict
    result: str
    text: str = ""


@dataclass
class AgentOperator:
    """Callable policy for `run_scenario`."""

    provider_name: str = "auto"
    model: str = "claude-haiku-4-5-20251001"
    max_turns: int = 8
    require_what_if: bool = True
    give_sensitivities: bool = True
    name: str = "agent"
    transcript: list[Turn] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    # reliability counters, reported alongside the load numbers
    stats: dict = field(default_factory=lambda: {
        "turns": 0, "unparseable": 0, "invalid_actions": 0,
        "guardrail_rejections": 0, "what_if_calls": 0, "applied": 0,
    })
    _provider: object | None = None
    on_turn: object | None = None

    def __post_init__(self):
        if self._provider is None:
            self._provider = get_provider(self.provider_name, self.model)
        self.name = f"agent_{getattr(self._provider, 'kind', 'x')}_{self.model}"

    # ---------- tool execution ----------

    def _run_tool(self, sim: CascadeSim, tool: str, args: dict, checked: set) -> tuple[str, bool]:
        """Returns (observation, should_stop)."""
        if tool == "relief_options":
            if not self.give_sensitivities:
                return "relief_options is not available in this configuration", False
            line = args.get("line")
            if not isinstance(line, int) or line not in sim.net.line.index:
                return f"relief_options needs a valid line id; got {line!r}", False
            return json.dumps(relief_options(sim, line), separators=(",", ":")), False

        if tool == "what_if":
            acts = args.get("actions") or []
            self.stats["what_if_calls"] += 1
            out = what_if(sim, acts)
            self.stats["invalid_actions"] += len(out.get("rejected") or [])
            if out["overloaded_after"] == 0 or out["would_lose_mw"] <= out["currently_lost_mw"]:
                checked.add(json.dumps(acts, sort_keys=True))
            return json.dumps(out, separators=(",", ":")), False

        if tool == "apply_actions":
            acts = args.get("actions") or []
            if self.require_what_if and acts:
                # a plan may only be committed if simulating it first showed it
                # beats doing nothing; this is enforced here, not requested in
                # the prompt
                outcome = what_if(sim, acts)
                baseline = what_if(sim, [])
                if outcome["would_lose_mw"] > baseline["would_lose_mw"]:
                    self.stats["guardrail_rejections"] += 1
                    return (
                        "REJECTED by guardrail: simulating this plan gives "
                        f"{outcome['would_lose_mw']} MW lost versus "
                        f"{baseline['would_lose_mw']} MW if you do nothing. "
                        "Try a different plan or call done.", False
                    )
            res = dispatch(sim, "apply_actions", args)
            self.stats["invalid_actions"] += len(res.data.get("rejected") or [])
            self.stats["applied"] += len(res.data.get("applied") or [])
            return f"{res.detail} | {json.dumps(res.data, separators=(',', ':'))}", False

        if tool == "done":
            return "turn ended", True

        if tool == "get_grid_state":
            return json.dumps(grid_state(sim), separators=(",", ":")), False

        return (
            f"unknown tool {tool!r}. Valid tools: "
            + ", ".join(f"{k} {v}" for k, v in TOOL_HINT.items()),
            False,
        )

    # ---------- the per-tick loop ----------

    def __call__(self, sim: CascadeSim) -> list[dict]:
        state = grid_state(sim)
        forecast = what_if(sim, [])
        messages = [{
            "role": "user",
            "content": (
                f"Tick {sim.tick}. Grid state:\n"
                f"{json.dumps(state, separators=(',', ':'))}\n\n"
                f"If you do nothing, the cascade settles with "
                f"{forecast['would_lose_mw']} MW of load lost "
                f"({forecast['lines_tripped_after']} lines out).\n"
                "Decide what to do. One JSON tool call."
            ),
        }]

        actions: list[dict] = []
        checked: set = set()
        for _ in range(self.max_turns):
            try:
                reply = self._provider.complete(SYSTEM, messages)
            except Exception as e:
                self.transcript.append(Turn(sim.tick, None, {}, f"provider error: {e}"))
                break

            self.stats["turns"] += 1
            if reply.tool is None:
                self.stats["unparseable"] += 1
                messages.append({"role": "assistant", "content": reply.text[:400]})
                messages.append({"role": "user", "content":
                                 "That was not a tool call. Reply with exactly one JSON "
                                 'object like {"tool":"get_grid_state","args":{}}'})
                self.transcript.append(Turn(sim.tick, None, {}, "unparseable", reply.text[:200]))
                continue

            obs, stop = self._run_tool(sim, reply.tool, reply.args, checked)
            self.transcript.append(Turn(sim.tick, reply.tool, reply.args, obs[:600],
                                        reply.text[:200]))
            if self.on_turn:
                self.on_turn(self.transcript[-1])

            if reply.tool == "apply_actions" and not obs.startswith("REJECTED"):
                for a in reply.args.get("actions") or []:
                    actions.append({"tick": sim.tick, "policy": self.name, "action": a,
                                    "result": obs[:200]})
            if stop:
                break
            messages.append({"role": "assistant", "content": reply.raw or reply.text[:300]})
            messages.append({"role": "user", "content": obs[:2000]})

        u: Usage = self._provider.usage
        self.usage = u.as_dict()
        return actions
