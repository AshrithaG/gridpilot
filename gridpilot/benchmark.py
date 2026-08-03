"""Curate a fixed scenario benchmark.

Screening matters: many correlated faults are absorbed by the grid with no
load loss at all. A benchmark of only damaging incidents would overstate how
useful an operator is; a benchmark of only benign ones would hide it. So the
set is deliberately mixed, and the benign half is kept as a trap for the
over-eager-operator failure mode (shedding load that did not need shedding).
"""

from __future__ import annotations

import json
from pathlib import Path

from gridpilot.runner import run_scenario
from gridpilot.scenarios import make_scenario

BENCH = Path("results/benchmark.json")


def build(n_damaging: int = 30, n_benign: int = 20, max_seeds: int = 400) -> dict:
    damaging, benign = [], []
    for seed in range(max_seeds):
        if len(damaging) >= n_damaging and len(benign) >= n_benign:
            break
        sc = make_scenario(seed)
        r = run_scenario(sc)
        row = {
            "seed": seed,
            "id": sc.id,
            "kind": sc.kind,
            "stress": sc.stress,
            "description": sc.description,
            "schedule": {str(k): v for k, v in sc.schedule.items()},
            "no_action_load_lost_mw": r.load_lost_mw,
            "no_action_buses_dark": r.buses_dark,
            "no_action_lines_tripped": r.lines_tripped,
        }
        if r.load_lost_mw > 1.0:
            if len(damaging) < n_damaging:
                damaging.append(row)
        elif len(benign) < n_benign:
            benign.append(row)

    out = {
        "damaging": damaging,
        "benign": benign,
        "note": "damaging = no-action baseline loses >1 MW of load",
    }
    BENCH.parent.mkdir(parents=True, exist_ok=True)
    BENCH.write_text(json.dumps(out, indent=2))
    return out


def load() -> dict:
    return json.loads(BENCH.read_text())


def seeds(which: str = "all") -> list[int]:
    b = load()
    if which == "damaging":
        return [r["seed"] for r in b["damaging"]]
    if which == "benign":
        return [r["seed"] for r in b["benign"]]
    return [r["seed"] for r in b["damaging"] + b["benign"]]


if __name__ == "__main__":
    import numpy as np

    out = build()
    dm = [r["no_action_load_lost_mw"] for r in out["damaging"]]
    print(f"damaging: {len(dm)} scenarios, no-action loss "
          f"mean {np.mean(dm):.1f} MW, median {np.median(dm):.1f}, max {max(dm):.1f}")
    print(f"benign:   {len(out['benign'])} scenarios with no load loss under no action")
    kinds: dict[str, int] = {}
    for r in out["damaging"] + out["benign"]:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("kinds:", kinds)
