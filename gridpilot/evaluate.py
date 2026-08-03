"""Batch evaluation: every policy against the same scenarios.

Scenarios run in a thread pool because the expensive part is either a
subprocess or an HTTP call, and each scenario owns its own grid copy, so there
is nothing shared to race on.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from gridpilot.benchmark import load as load_bench
from gridpilot.policies import GreedyShed, GreedyShedWithCheck, RedispatchRelief
from gridpilot.runner import run_scenario
from gridpilot.scenarios import make_scenario

OUT = Path("results")


def make_policy(spec: str, model: str):
    if spec == "no_action":
        return None
    if spec == "greedy_shed":
        return GreedyShed()
    if spec == "greedy_checked":
        return GreedyShedWithCheck()
    if spec == "redispatch_relief":
        return RedispatchRelief()
    from gridpilot.agent import AgentOperator

    if spec == "agent":
        return AgentOperator(model=model)
    if spec == "agent_no_guardrail":
        p = AgentOperator(model=model, require_what_if=False)
        p.name = "agent_no_guardrail"
        return p
    if spec == "agent_no_sensitivity":
        p = AgentOperator(model=model, give_sensitivities=False)
        p.name = "agent_no_sensitivity"
        return p
    raise ValueError(f"unknown policy {spec}")


def run_one(spec: str, seed: int, model: str, max_decision_points: int) -> dict:
    policy = make_policy(spec, model)
    t0 = time.time()
    r = run_scenario(make_scenario(seed), policy,
                     max_decision_points=max_decision_points)
    row = r.as_dict()
    row["policy_spec"] = spec
    row["seed"] = seed
    row["seconds"] = round(time.time() - t0, 1)
    row["stats"] = getattr(policy, "stats", {})
    row.pop("events", None)
    return row


def evaluate(specs: list[str], seeds: list[int], model: str, workers: int,
             max_decision_points: int) -> list[dict]:
    jobs = [(s, seed) for s in specs for seed in seeds]
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(run_one, s, seed, model, max_decision_points): (s, seed)
                for s, seed in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            spec, seed = futs[fut]
            try:
                rows.append(fut.result())
            except Exception as e:
                print(f"  [{i}/{len(jobs)}] {spec} s{seed} FAILED: {type(e).__name__}: {e}")
                continue
            r = rows[-1]
            print(f"  [{i}/{len(jobs)}] {spec:22s} s{seed:03d} "
                  f"lost {r['load_lost_mw']:7.1f} MW  ({r['seconds']:.0f}s)")
    return rows


def summarize(rows: list[dict], bench: dict) -> dict:
    damaging = {r["seed"] for r in bench["damaging"]}
    no_action = {r["seed"]: r["no_action_load_lost_mw"] for r in bench["damaging"]}
    out: dict[str, dict] = {}
    for spec in sorted({r["policy_spec"] for r in rows}):
        mine = [r for r in rows if r["policy_spec"] == spec]
        dmg = [r for r in mine if r["seed"] in damaging]
        ben = [r for r in mine if r["seed"] not in damaging]
        losses = np.array([r["load_lost_mw"] for r in dmg]) if dmg else np.array([0.0])
        saved = np.array([max(no_action[r["seed"]] - r["load_lost_mw"], -1e9) for r in dmg]) \
            if dmg else np.array([0.0])
        base = np.array([no_action[r["seed"]] for r in dmg]) if dmg else np.array([1.0])
        stats_keys = ["turns", "unparseable", "invalid_actions", "guardrail_rejections",
                      "what_if_calls", "applied"]
        agg = {k: int(sum(r.get("stats", {}).get(k, 0) for r in mine)) for k in stats_keys}
        toks = [r.get("usage", {}) for r in mine]
        out[spec] = {
            "n_damaging": len(dmg),
            "n_benign": len(ben),
            "mean_load_lost_mw": round(float(losses.mean()), 1),
            "median_load_lost_mw": round(float(np.median(losses)), 1),
            "max_load_lost_mw": round(float(losses.max()), 1),
            "mean_mw_saved_vs_no_action": round(float(saved.mean()), 1),
            "pct_of_no_action_loss_avoided": round(
                100.0 * float(saved.sum()) / max(float(base.sum()), 1e-9), 1),
            "scenarios_fully_contained": int(sum(1 for r in dmg if r["load_lost_mw"] < 1.0)),
            "scenarios_made_worse": int(sum(
                1 for r in dmg if r["load_lost_mw"] > no_action[r["seed"]] + 0.5)),
            "benign_mean_load_lost_mw": round(
                float(np.mean([r["load_lost_mw"] for r in ben])) if ben else 0.0, 1),
            "benign_scenarios_damaged": int(sum(1 for r in ben if r["load_lost_mw"] > 0.5)),
            "mean_seconds": round(float(np.mean([r["seconds"] for r in mine])), 1),
            "counters": agg,
            "input_tokens": int(sum(u.get("input_tokens", 0) for u in toks)),
            "output_tokens": int(sum(u.get("output_tokens", 0) for u in toks)),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policies", nargs="+",
                    default=["no_action", "greedy_shed", "redispatch_relief"])
    ap.add_argument("--n-damaging", type=int, default=30)
    ap.add_argument("--n-benign", type=int, default=20)
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-decision-points", type=int, default=4)
    ap.add_argument("--tag", default="run")
    args = ap.parse_args()

    bench = load_bench()
    seeds = ([r["seed"] for r in bench["damaging"]][: args.n_damaging]
             + [r["seed"] for r in bench["benign"]][: args.n_benign])
    print(f"{len(args.policies)} policies x {len(seeds)} scenarios "
          f"({args.n_damaging} damaging, {args.n_benign} benign)")

    t0 = time.time()
    rows = evaluate(args.policies, seeds, args.model, args.workers,
                    args.max_decision_points)
    summary = summarize(rows, bench)

    OUT.mkdir(exist_ok=True)
    (OUT / f"eval_{args.tag}.json").write_text(json.dumps(
        {"config": vars(args), "summary": summary, "rows": rows}, indent=2))

    print(f"\ndone in {time.time() - t0:.0f}s\n")
    hdr = f"{'policy':24s} {'mean lost':>10s} {'max':>7s} {'% avoided':>10s} " \
          f"{'contained':>10s} {'worse':>6s} {'benign hurt':>12s}"
    print(hdr)
    print("-" * len(hdr))
    for spec, s in summary.items():
        print(f"{spec:24s} {s['mean_load_lost_mw']:10.1f} {s['max_load_lost_mw']:7.1f} "
              f"{s['pct_of_no_action_loss_avoided']:10.1f} "
              f"{s['scenarios_fully_contained']:10d} {s['scenarios_made_worse']:6d} "
              f"{s['benign_scenarios_damaged']:12d}")
    print(f"\nwrote results/eval_{args.tag}.json")


if __name__ == "__main__":
    main()
