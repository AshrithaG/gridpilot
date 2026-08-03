"""Seeded incident scenarios.

Random line pairs almost never interact, so the interesting contingencies are
correlated ones: two lines out of the same substation, or a storm walking
through one corridor. Each scenario is fully determined by its seed, so the
agent, the heuristic, and the do-nothing baseline all face the identical
incident.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from gridpilot.grid import DATA, load_case

CUTSET_CACHE = Path(DATA) / "case118_cutsets.json"


@dataclass
class Scenario:
    id: str
    kind: str  # substation | storm | peak_n1
    stress: float
    # tick -> lines that trip at that tick (tick 0 is the initiating event)
    schedule: dict[int, list[int]] = field(default_factory=dict)
    description: str = ""

    def trips_at(self, tick: int) -> list[int]:
        return self.schedule.get(tick, [])

    @property
    def horizon(self) -> int:
        return max(self.schedule) if self.schedule else 0


def _lines_by_bus(net) -> dict[int, list[int]]:
    out = defaultdict(list)
    for i in net.line.index:
        out[int(net.line.at[i, "from_bus"])].append(int(i))
        out[int(net.line.at[i, "to_bus"])].append(int(i))
    return out


def find_cutsets(min_minor_island: int = 8, max_cut: int = 5, tries: int = 400) -> list[dict]:
    """Line sets whose simultaneous loss splits the grid into two substantial
    islands. These are the incidents that actually hurt: the smaller island is
    usually generation-deficient and has to shed. Cached because the search
    runs a few hundred min-cut queries.
    """
    if CUTSET_CACHE.exists():
        return json.loads(CUTSET_CACHE.read_text())

    import networkx as nx

    net = load_case()
    g = nx.Graph()
    for i in net.line.index:
        g.add_edge(int(net.line.at[i, "from_bus"]), int(net.line.at[i, "to_bus"]), line=int(i))
    for i in net.trafo.index:
        g.add_edge(int(net.trafo.at[i, "hv_bus"]), int(net.trafo.at[i, "lv_bus"]), line=None)

    rng = random.Random(3)
    buses = list(g.nodes)
    found: dict[tuple, list[int]] = {}
    for _ in range(tries):
        a, b = rng.sample(buses, 2)
        try:
            cut = nx.minimum_edge_cut(g, a, b)
        except Exception:
            continue
        lines = [g[u][v].get("line") for u, v in cut]
        if any(ln is None for ln in lines) or not (3 <= len(cut) <= max_cut):
            continue
        h = g.copy()
        h.remove_edges_from(cut)
        sizes = sorted((len(c) for c in nx.connected_components(h)), reverse=True)
        if len(sizes) > 1 and sizes[1] >= min_minor_island:
            found[tuple(sorted(lines))] = sizes[:3]

    out = [{"lines": list(k), "island_sizes": v} for k, v in found.items()]
    out.sort(key=lambda d: -d["island_sizes"][1])
    CUTSET_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CUTSET_CACHE.write_text(json.dumps(out, indent=2))
    return out


def make_scenario(seed: int) -> Scenario:
    rng = random.Random(seed)
    net = load_case()
    by_bus = _lines_by_bus(net)
    multi = [b for b, ls in by_bus.items() if len(ls) >= 2]

    kind = rng.choice(["substation", "substation", "storm", "peak_n1", "corridor"])
    # limits are sized for N-1 security at nominal load, so ambient stress
    # stays low enough that the base case is clean; the difficulty comes from
    # correlated multi-line faults, not from starting out overloaded
    stress = round(rng.uniform(1.0, 1.03), 3)

    if kind == "substation":
        bus = rng.choice(multi)
        pair = rng.sample(by_bus[bus], 2)
        return Scenario(
            id=f"s{seed:03d}",
            kind=kind,
            stress=stress,
            schedule={0: pair},
            description=f"busbar fault at substation {bus}: lines {pair[0]} and {pair[1]} "
                        f"trip simultaneously",
        )

    if kind == "corridor":
        cuts = find_cutsets()
        if cuts:
            cut = rng.choice(cuts)
            lines = cut["lines"]
            # the corridor fails progressively, which is what gives an operator
            # a chance to act before the split completes
            schedule = {0: lines[:2]}
            for k, ln in enumerate(lines[2:]):
                schedule[2 * (k + 1)] = [ln]
            return Scenario(
                id=f"s{seed:03d}",
                kind=kind,
                stress=stress,
                schedule=schedule,
                description=f"corridor failure on lines {lines}: completing the cut "
                            f"splits the system into islands of about "
                            f"{cut['island_sizes'][0]} and {cut['island_sizes'][1]} buses",
            )
        kind = "substation"

    if kind == "storm":
        # walk outward from a seed bus, dropping a line every couple of ticks
        bus = rng.choice(multi)
        picked: list[int] = []
        frontier = [bus]
        seen = {bus}
        while len(picked) < 4 and frontier:
            b = frontier.pop(0)
            for ln in by_bus.get(b, []):
                if ln not in picked:
                    picked.append(ln)
                    for nb in (int(net.line.at[ln, "from_bus"]), int(net.line.at[ln, "to_bus"])):
                        if nb not in seen:
                            seen.add(nb)
                            frontier.append(nb)
                    break
        schedule = {i * 2: [ln] for i, ln in enumerate(picked)}
        return Scenario(
            id=f"s{seed:03d}",
            kind=kind,
            stress=stress,
            schedule=schedule,
            description=f"storm front near bus {bus}: lines {picked} trip over "
                        f"{2 * (len(picked) - 1)} ticks",
        )

    # peak_n1: heavier load, then two trips on the most loaded corridors
    import numpy as np
    import pandapower as pp

    peak = 1.05
    hot = load_case(peak)
    pp.rundcpp(hot)
    loading = np.nan_to_num(hot.res_line.loading_percent.values)
    top = [int(hot.line.index[k]) for k in loading.argsort()[::-1][:14]]
    picked = rng.sample(top, 2)
    return Scenario(
        id=f"s{seed:03d}",
        kind=kind,
        stress=peak,
        schedule={0: [picked[0]], 3: [picked[1]]},
        description=f"peak demand ({peak:.0%} of nominal): line {picked[0]} trips, "
                    f"then line {picked[1]} on another loaded corridor 3 ticks later",
    )


def scenario_set(n: int = 50, start: int = 0) -> list[Scenario]:
    return [make_scenario(s) for s in range(start, start + n)]
