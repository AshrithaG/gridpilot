"""Non-LLM operator policies: the bar the agent has to clear.

A do-nothing baseline alone is too easy to beat, so the main comparison is a
greedy relief heuristic of the kind a control-room rule book would encode:
find the overloads, shed nearby load in proportion to the excess, stop when
the overload clears.

Policies split planning from committing. `plan()` is pure, which lets the
checked variant run the same plan through the what-if guardrail first and
measure how much of the benefit comes from the guardrail rather than the
heuristic.
"""

from __future__ import annotations

import numpy as np

from gridpilot.cascade import CascadeSim
from gridpilot.tools import PROTECTED_BUSES, ToolError, apply_one, what_if


def commit(sim: CascadeSim, actions: list[dict], policy_name: str) -> list[dict]:
    log = []
    for a in actions:
        try:
            log.append({"tick": sim.tick, "policy": policy_name, "action": a,
                        "result": apply_one(sim, a)})
        except (ToolError, ValueError) as e:
            log.append({"tick": sim.tick, "policy": policy_name, "action": a,
                        "error": str(e)})
    return log


class NoAction:
    name = "no_action"

    def plan(self, sim: CascadeSim) -> list[dict]:
        return []

    def __call__(self, sim: CascadeSim) -> list[dict]:
        return []


class GreedyShed:
    """Shed load electrically close to each overloaded line, sized to the
    excess flow. No lookahead: it reacts to the overload in front of it."""

    name = "greedy_shed"

    def __init__(self, gain: float = 1.0, max_actions: int = 3, hops: int = 2):
        self.gain = gain
        self.max_actions = max_actions
        self.hops = hops

    def _neighbourhood(self, sim: CascadeSim, line: int) -> list[int]:
        net = sim.net
        seen = {int(net.line.at[line, "from_bus"]), int(net.line.at[line, "to_bus"])}
        frontier = set(seen)
        live = net.line[net.line.in_service]
        pairs = list(zip(live.from_bus.astype(int), live.to_bus.astype(int)))
        for _ in range(self.hops):
            nxt = set()
            for f, t in pairs:
                if f in frontier:
                    nxt.add(t)
                if t in frontier:
                    nxt.add(f)
            frontier = nxt - seen
            seen |= nxt
        return list(seen)

    def plan(self, sim: CascadeSim) -> list[dict]:
        load = sim.loadings()
        over = [k for k in range(len(load)) if load[k] > 100.0]
        if not over:
            return []
        net = sim.net
        over.sort(key=lambda k: -load[k])
        actions: list[dict] = []
        for k in over:
            if len(actions) >= self.max_actions:
                break
            line = int(net.line.index[k])
            flow = float(abs(np.nan_to_num(net.res_line.p_from_mw.get(line, 0.0))))
            excess = (load[k] - 100.0) / max(load[k], 1.0) * flow * self.gain
            if excess < 1.0:
                continue
            near = self._neighbourhood(sim, line)
            live = net.load[
                net.load.in_service
                & net.load.bus.isin(near)
                & ~net.load.bus.isin(PROTECTED_BUSES)
            ]
            if live.empty:
                continue
            remaining = excess
            for bus, avail in live.groupby("bus").p_mw.sum().sort_values(
                ascending=False
            ).items():
                if remaining < 1.0 or len(actions) >= self.max_actions:
                    break
                mw = float(min(remaining, float(avail), 120.0))
                actions.append({"type": "shed_load", "bus": int(bus), "mw": round(mw, 1)})
                remaining -= mw
        return actions

    def __call__(self, sim: CascadeSim) -> list[dict]:
        return commit(sim, self.plan(sim), self.name)


class RedispatchRelief:
    """Relieve overloads by moving generation first, and only shed load if
    redispatch cannot clear the overload. Every plan is checked against the
    do-nothing counterfactual before it is committed.

    This is the strong baseline: it uses the same sensitivity information and
    the same what-if guardrail the agent gets, so beating it requires actual
    judgement rather than just access to better tools.
    """

    name = "redispatch_relief"

    def __init__(self, margin_pct: float = 8.0, max_actions: int = 3):
        self.margin_pct = margin_pct
        self.max_actions = max_actions
        self._shedder = GreedyShed(max_actions=max_actions)

    def plan(self, sim: CascadeSim) -> list[dict]:
        from gridpilot.sensitivity import relief_options

        load = sim.loadings()
        over = np.where(load > 100.0)[0]
        if not len(over):
            return []
        worst = int(sim.net.line.index[over[np.argmax(load[over])]])
        opts = relief_options(sim, worst)
        target = opts["excess_pct"] + self.margin_pct

        actions: list[dict] = []
        for s in opts["raise_these_to_relieve"]:
            per = abs(s["d_loading_per_mw"])
            if per < 1e-5:
                continue
            headroom = s["max_mw"] - s["current_mw"]
            step = min(target / per, headroom)
            if step > 1.0:
                actions.append({"type": "redispatch", "gen": s["gen"],
                                "mw": round(s["current_mw"] + step, 1)})
                target -= step * per
            if target <= 0 or len(actions) >= self.max_actions:
                break
        if target > 0:
            for s in opts["lower_these_to_relieve"]:
                per = abs(s["d_loading_per_mw"])
                if per < 1e-5:
                    continue
                step = min(target / per, s["current_mw"])
                if step > 1.0:
                    actions.append({"type": "redispatch", "gen": s["gen"],
                                    "mw": round(s["current_mw"] - step, 1)})
                    target -= step * per
                if target <= 0 or len(actions) >= self.max_actions:
                    break
        return actions

    def __call__(self, sim: CascadeSim) -> list[dict]:
        baseline = what_if(sim, [])["would_lose_mw"]
        plan = self.plan(sim)
        if plan and what_if(sim, plan)["would_lose_mw"] < baseline:
            return commit(sim, plan, self.name)
        # redispatch cannot help; fall back to targeted shedding, still checked
        shed = self._shedder.plan(sim)
        if shed and what_if(sim, shed)["would_lose_mw"] < baseline:
            return commit(sim, shed, self.name)
        return []


class GreedyShedWithCheck(GreedyShed):
    """The same heuristic, but it simulates its own plan first and abandons it
    unless the outcome actually improves on doing nothing."""

    name = "greedy_checked"

    def __call__(self, sim: CascadeSim) -> list[dict]:
        plan = self.plan(sim)
        if not plan:
            return []
        if what_if(sim, plan)["would_lose_mw"] >= what_if(sim, [])["would_lose_mw"]:
            return [{"tick": sim.tick, "policy": self.name, "action": None,
                     "result": "plan rejected by what-if check"}]
        return commit(sim, plan, self.name)
