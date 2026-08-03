"""Run one scenario to completion under a given operator policy.

The sim is turn-based: each tick the scenario may trip lines, protection may
trip more, and then the operator (agent, heuristic, or nobody) may act. That
mirrors how control-room decisions actually work -- minutes, not milliseconds
-- and it means LLM latency never distorts the physics.

The operator is called at *decision points*, not every tick: when something
just tripped, or while an overload is live. That matters for more than cost.
Some incidents (a corridor splitting the grid) destroy load through islanding
without ever overloading a line, so a policy that only reacts to overloads
would never be consulted at all -- and would look deceptively good.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gridpilot.cascade import CascadeSim
from gridpilot.grid import load_case
from gridpilot.scenarios import Scenario

MAX_TICKS = 24
MAX_DECISION_POINTS = 6


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
    decision_points: int = 0
    actions: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def run_scenario(scenario: Scenario, policy=None, max_ticks: int = MAX_TICKS,
                 max_decision_points: int = MAX_DECISION_POINTS,
                 on_state=None) -> RunResult:
    """`policy` is a callable(sim) -> list of action records, or None for the
    do-nothing baseline. `on_state` receives a snapshot after every tick."""
    sim = CascadeSim(load_case(scenario.stress))
    actions: list[dict] = []
    usage: dict = {}
    decisions = 0

    for tick in range(max_ticks):
        sim.tick = tick

        scheduled = [ln for ln in scenario.trips_at(tick)
                     if sim.net.line.at[ln, "in_service"]]
        for line in scheduled:
            sim.trip_line(line, reason=f"{scenario.kind} event")

        # The operator's window: the disturbance has happened and its overloads
        # are visible, but the relays have not acted yet. Emergency control has
        # to beat protection to be worth anything, so this ordering is the
        # whole game -- acting after the trip is just cleanup.
        overloaded = not sim.settled()
        if (policy is not None
                and decisions < max_decision_points
                and (scheduled or overloaded)):
            decisions += 1
            actions.extend(policy(sim) or [])
            if getattr(policy, "usage", None):
                usage = policy.usage

        protection_tripped = sim.step()

        if on_state is not None:
            on_state(sim.snapshot())

        more_coming = tick < scenario.horizon
        if sim.settled() and not more_coming and not protection_tripped and not scheduled:
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
        decision_points=decisions,
        actions=actions,
        events=[e.__dict__ for e in m.events],
        usage=usage,
    )
