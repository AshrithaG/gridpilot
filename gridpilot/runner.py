"""Run one scenario to completion under a given operator policy.

The sim is turn-based: each tick the scenario may trip lines, protection may
trip more, and then the operator (agent, heuristic, or nobody) gets to act.
That mirrors how control-room decisions actually work — minutes, not
milliseconds — and it means LLM latency never distorts the physics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gridpilot.cascade import CascadeSim
from gridpilot.grid import load_case
from gridpilot.scenarios import Scenario

MAX_TICKS = 24


@dataclass
class RunResult:
    scenario: str
    policy: str
    load_lost_mw: float
    load_served_mw: float
    buses_dark: int
    lines_tripped: int
    operator_shed_mw: float
    ticks: int
    contained: bool
    actions: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def run_scenario(scenario: Scenario, policy=None, max_ticks: int = MAX_TICKS,
                 on_state=None) -> RunResult:
    """`policy` is a callable(sim) -> list[dict] of actions taken, or None for
    the do-nothing baseline. `on_state` gets a snapshot after every tick."""
    sim = CascadeSim(load_case(scenario.stress))
    actions: list[dict] = []
    usage: dict = {}

    for tick in range(max_ticks):
        for line in scenario.trips_at(tick):
            if sim.net.line.at[line, "in_service"]:
                sim.trip_line(line, reason=f"{scenario.kind} event")

        sim.tick = tick
        sim.step()

        if policy is not None and not sim.settled():
            taken = policy(sim) or []
            actions.extend(taken)
            if getattr(policy, "usage", None):
                usage = policy.usage

        if on_state is not None:
            on_state(sim.snapshot())

        if sim.settled() and tick >= scenario.horizon:
            break

    m = sim.metrics()
    return RunResult(
        scenario=scenario.id,
        policy=getattr(policy, "name", "no_action" if policy is None else "policy"),
        load_lost_mw=m.load_lost_mw,
        load_served_mw=m.load_served_mw,
        buses_dark=m.buses_dark,
        lines_tripped=m.lines_tripped,
        operator_shed_mw=m.shed_by_operator_mw,
        ticks=sim.tick,
        contained=m.overloaded_lines == 0 and m.load_lost_mw < 1.0,
        actions=actions,
        events=[e.__dict__ for e in m.events],
        usage=usage,
    )
