"""The operator's action surface.

Everything an operator can do is a tool call, and every mutating call goes
through the same two gates: schema/permission validation, then (optionally)
a what-if simulation on a cloned grid so the caller can see the consequence
before committing. That second gate is what keeps a plausible-but-wrong
decision from becoming a blackout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from gridpilot.cascade import CascadeSim

# buses that must never be shed: hospitals, water treatment, and similar
PROTECTED_BUSES = {5, 21, 49, 77, 100}

MAX_SHED_PER_ACTION_MW = 120.0
MAX_ACTIONS_PER_TICK = 4


class ToolError(Exception):
    pass


@dataclass
class ToolResult:
    ok: bool
    detail: str
    data: dict


TOOL_SCHEMAS = [
    {
        "name": "get_grid_state",
        "description": (
            "Current grid conditions: overloaded lines with their loading percent, "
            "island structure, load served vs lost, and how many ticks each overloaded "
            "line has left before protection trips it."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "what_if",
        "description": (
            "Simulate a list of actions on a copy of the grid and return the outcome "
            "WITHOUT applying them. Use this before committing to anything: it reports "
            "the load that would be lost, lines that would still be overloaded, and "
            "whether the cascade would stop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "description": "Actions to test, same shape as apply_actions.",
                    "items": {"type": "object"},
                }
            },
            "required": ["actions"],
        },
    },
    {
        "name": "apply_actions",
        "description": (
            "Commit actions to the real grid. Each action is one of: "
            '{"type":"shed_load","bus":int,"mw":float} to curtail demand, '
            '{"type":"redispatch","gen":int,"mw":float} to move a generator to a new '
            'setpoint, {"type":"open_line","line":int} to open a line, '
            '{"type":"close_line","line":int} to reclose one.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "actions": {"type": "array", "items": {"type": "object"}},
                "reasoning": {
                    "type": "string",
                    "description": "One sentence on why these actions, for the operator log.",
                },
            },
            "required": ["actions"],
        },
    },
    {
        "name": "done",
        "description": "Finish this tick with no further action.",
        "input_schema": {
            "type": "object",
            "properties": {"reasoning": {"type": "string"}},
            "required": [],
        },
    },
]


def validate(action: dict) -> dict:
    """Reject malformed or forbidden actions before they touch the grid."""
    if not isinstance(action, dict):
        raise ToolError(f"action must be an object, got {type(action).__name__}")
    kind = action.get("type")
    if kind == "shed_load":
        bus, mw = action.get("bus"), action.get("mw")
        if not isinstance(bus, int):
            raise ToolError("shed_load needs an integer 'bus'")
        if not isinstance(mw, (int, float)) or mw <= 0:
            raise ToolError("shed_load needs a positive 'mw'")
        if bus in PROTECTED_BUSES:
            raise ToolError(f"bus {bus} is protected (critical load) and cannot be shed")
        if mw > MAX_SHED_PER_ACTION_MW:
            raise ToolError(
                f"shed_load capped at {MAX_SHED_PER_ACTION_MW:.0f} MW per action, got {mw}"
            )
        return {"type": "shed_load", "bus": int(bus), "mw": float(mw)}
    if kind == "redispatch":
        gen, mw = action.get("gen"), action.get("mw")
        if not isinstance(gen, int):
            raise ToolError("redispatch needs an integer 'gen'")
        if not isinstance(mw, (int, float)) or mw < 0:
            raise ToolError("redispatch needs a non-negative 'mw'")
        return {"type": "redispatch", "gen": int(gen), "mw": float(mw)}
    if kind in ("open_line", "close_line"):
        line = action.get("line")
        if not isinstance(line, int):
            raise ToolError(f"{kind} needs an integer 'line'")
        return {"type": kind, "line": int(line)}
    raise ToolError(
        f"unknown action type {kind!r}; expected shed_load, redispatch, open_line or close_line"
    )


def apply_one(sim: CascadeSim, action: dict) -> str:
    a = validate(action)
    if a["type"] == "shed_load":
        got = sim.shed_load(a["bus"], a["mw"])
        return f"shed {got:.1f} MW at bus {a['bus']}"
    if a["type"] == "redispatch":
        got = sim.redispatch(a["gen"], a["mw"])
        return f"generator {a['gen']} set to {got:.1f} MW"
    if a["type"] == "open_line":
        sim.trip_line(a["line"], reason="operator")
        return f"opened line {a['line']}"
    sim.close_line(a["line"])
    return f"reclosed line {a['line']}"


def grid_state(sim: CascadeSim, max_lines: int = 14) -> dict:
    """What the operator sees. Deliberately compact: an operator does not need
    all 173 line flows, they need the ones that are in trouble."""
    net = sim.net
    load = sim.loadings()
    order = np.argsort(load)[::-1]
    hot = []
    for k in order[:max_lines]:
        if load[k] < 50:
            break
        idx = int(net.line.index[k])
        ticks_left = max(0, sim.trip_delay - int(sim._over_count[k])) if load[k] > 100 else None
        hot.append({
            "line": idx,
            "from_bus": int(net.line.at[idx, "from_bus"]),
            "to_bus": int(net.line.at[idx, "to_bus"]),
            "loading_pct": round(float(load[k]), 1),
            "ticks_until_trip": ticks_left,
        })

    m = sim.metrics()
    islands = getattr(sim, "_islands", [])
    island_info = []
    for isl in sorted(islands, key=len, reverse=True)[:4]:
        loads = net.load[net.load.in_service & net.load.bus.isin(isl)]
        gens = net.gen[net.gen.in_service & net.gen.bus.isin(isl)]
        island_info.append({
            "buses": len(isl),
            "load_mw": round(float(loads.p_mw.sum()), 1),
            "generation_mw": round(float(gens.p_mw.sum()), 1),
        })

    # biggest sheddable loads, so the operator does not have to guess bus numbers
    live = net.load[net.load.in_service & ~net.load.bus.isin(PROTECTED_BUSES)]
    big = live.groupby("bus").p_mw.sum().sort_values(ascending=False).head(8)
    return {
        "tick": sim.tick,
        "overloaded_line_count": m.overloaded_lines,
        "hot_lines": hot,
        "islands": island_info,
        "load_served_mw": m.load_served_mw,
        "load_lost_mw": m.load_lost_mw,
        "lines_out_of_service": int((~net.line.in_service).sum()),
        "sheddable_load_by_bus": {int(b): round(float(v), 1) for b, v in big.items()},
        "protected_buses": sorted(PROTECTED_BUSES),
        "trip_rule": f"a line over 100% trips after {sim.trip_delay} consecutive ticks, "
                     f"or immediately above {sim.instant_trip_pct:.0f}%",
    }


def what_if(sim: CascadeSim, actions: list[dict], settle_ticks: int = 6) -> dict:
    """Run actions on a clone and let the cascade play out, so the caller sees
    the consequence before committing. This is the guardrail that turns a
    guess into a checked decision."""
    twin = sim.clone()
    applied, errors = [], []
    for a in actions:
        try:
            applied.append(apply_one(twin, a))
        except (ToolError, ValueError) as e:
            errors.append(str(e))

    for _ in range(settle_ticks):
        twin.step()
        if twin.settled():
            break

    m = twin.metrics()
    base = sim.metrics()
    return {
        "applied": applied,
        "rejected": errors,
        "would_lose_mw": m.load_lost_mw,
        "currently_lost_mw": base.load_lost_mw,
        "delta_vs_no_action_mw": round(m.load_lost_mw - base.load_lost_mw, 1),
        "overloaded_after": m.overloaded_lines,
        "lines_tripped_after": m.lines_tripped,
        "buses_dark_after": m.buses_dark,
        "cascade_stops": m.overloaded_lines == 0,
    }


def no_action_forecast(sim: CascadeSim, settle_ticks: int = 6) -> dict:
    """What happens if the operator does nothing: the counterfactual every
    decision should be measured against."""
    return what_if(sim, [], settle_ticks=settle_ticks)


def dispatch(sim: CascadeSim, name: str, args: dict) -> ToolResult:
    if name == "get_grid_state":
        return ToolResult(True, "ok", grid_state(sim))
    if name == "what_if":
        acts = args.get("actions") or []
        if not isinstance(acts, list):
            return ToolResult(False, "actions must be a list", {})
        return ToolResult(True, "ok", what_if(sim, acts))
    if name == "apply_actions":
        acts = args.get("actions") or []
        if not isinstance(acts, list):
            return ToolResult(False, "actions must be a list", {})
        if len(acts) > MAX_ACTIONS_PER_TICK:
            return ToolResult(
                False, f"at most {MAX_ACTIONS_PER_TICK} actions per tick", {}
            )
        done, errs = [], []
        for a in acts:
            try:
                done.append(apply_one(sim, a))
            except (ToolError, ValueError) as e:
                errs.append(str(e))
        state = grid_state(sim)
        return ToolResult(
            not errs or bool(done),
            "; ".join(done) if done else "nothing applied",
            {"applied": done, "rejected": errs,
             "overloaded_now": state["overloaded_line_count"],
             "load_lost_mw": state["load_lost_mw"]},
        )
    if name == "done":
        return ToolResult(True, "finished", {})
    return ToolResult(False, f"unknown tool {name}", {})


def summarize(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))
