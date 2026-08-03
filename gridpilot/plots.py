"""Figures for the README."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RESULTS = Path("results")
FIGURES = RESULTS / "figures"

LABELS = {
    "no_action": "do nothing",
    "greedy_shed": "greedy load shedding",
    "greedy_checked": "greedy, what-if checked",
    "redispatch_relief": "redispatch heuristic",
    "agent": "LLM agent",
    "agent_no_guardrail": "agent, no what-if guardrail",
    "agent_no_sensitivity": "agent, no sensitivity tool",
}
COLORS = {
    "no_action": "#8a8f98",
    "greedy_shed": "#d64545",
    "greedy_checked": "#e0846f",
    "redispatch_relief": "#c8a02a",
    "agent": "#4b7ade",
    "agent_no_guardrail": "#8b5cd6",
    "agent_no_sensitivity": "#3f9d54",
}


def collect() -> tuple[dict, dict]:
    """Merge every eval_*.json into {policy: summary} and {policy: {seed: loss}}."""
    summaries, per_seed = {}, {}
    for path in sorted(RESULTS.glob("eval_*.json")):
        if "smoke" in path.name:
            continue
        blob = json.loads(path.read_text())
        for spec, s in blob["summary"].items():
            summaries[spec] = s
        for row in blob["rows"]:
            per_seed.setdefault(row["policy_spec"], {})[row["seed"]] = row["load_lost_mw"]
    return summaries, per_seed


def order(specs) -> list[str]:
    pref = ["no_action", "greedy_shed", "greedy_checked", "redispatch_relief", "agent",
            "agent_no_guardrail", "agent_no_sensitivity"]
    return [s for s in pref if s in specs] + [s for s in specs if s not in pref]


def bars(summaries: dict):
    specs = order(summaries)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))

    means = [summaries[s]["mean_load_lost_mw"] for s in specs]
    maxes = [summaries[s]["max_load_lost_mw"] for s in specs]
    x = np.arange(len(specs))
    axes[0].bar(x - 0.19, means, 0.38, label="mean", color=[COLORS.get(s, "#666") for s in specs])
    axes[0].bar(x + 0.19, maxes, 0.38, label="worst case",
                color=[COLORS.get(s, "#666") for s in specs], alpha=0.45)
    base = summaries.get("no_action", {}).get("mean_load_lost_mw")
    if base:
        axes[0].axhline(base, color="#8a8f98", ls="--", lw=1,
                        label=f"do-nothing mean ({base:.0f} MW)")
    axes[0].set_ylabel("load lost (MW)")
    axes[0].set_title("Load lost across 30 damaging incidents", fontsize=11)
    axes[0].set_xticks(x, [LABELS.get(s, s) for s in specs], rotation=20, ha="right", fontsize=9)
    axes[0].legend(fontsize=8)
    axes[0].grid(axis="y", alpha=0.25)

    avoided = [summaries[s]["pct_of_no_action_loss_avoided"] for s in specs]
    cols = ["#d64545" if v < 0 else COLORS.get(s, "#666") for s, v in zip(specs, avoided)]
    axes[1].barh(x, avoided, color=cols)
    axes[1].axvline(0, color="#444", lw=1)
    axes[1].set_yticks(x, [LABELS.get(s, s) for s in specs], fontsize=9)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("% of the do-nothing damage avoided")
    axes[1].set_title("Negative means the operator made it worse", fontsize=11)
    axes[1].grid(axis="x", alpha=0.25)
    span = max(abs(min(avoided)), abs(max(avoided))) or 1.0
    axes[1].set_xlim(min(min(avoided), 0) - 0.18 * span, max(max(avoided), 0) + 0.18 * span)
    for i, v in enumerate(avoided):
        # negative labels sit inside the bar so they never collide with the axis
        # text -- unless the bar is too short to hold them
        inside = v < 0 and abs(v) > 0.14 * span
        axes[1].text(v + 0.02 * span, i, f"{v:+.0f}%", va="center",
                     ha="left", fontsize=9,
                     color="#ffffff" if inside else "#222222",
                     fontweight="bold" if inside else "normal")

    fig.tight_layout()
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / "policies.png", dpi=150)
    print("wrote", FIGURES / "policies.png")


def per_scenario(per_seed: dict):
    bench = json.loads((RESULTS / "benchmark.json").read_text())
    dmg = [r for r in bench["damaging"]]
    dmg.sort(key=lambda r: -r["no_action_load_lost_mw"])
    seeds = [r["seed"] for r in dmg]
    base = [r["no_action_load_lost_mw"] for r in dmg]

    fig, ax = plt.subplots(figsize=(12, 4.6))
    x = np.arange(len(seeds))
    ax.plot(x, base, "o-", color="#8a8f98", lw=1.4, ms=4, label=LABELS["no_action"])
    for spec in ["greedy_shed", "redispatch_relief", "agent"]:
        if spec not in per_seed:
            continue
        ys = [per_seed[spec].get(s, np.nan) for s in seeds]
        ax.plot(x, ys, "o-", color=COLORS[spec], lw=1.4, ms=4, label=LABELS[spec], alpha=0.9)
    ax.set_xticks(x, [f"s{s:03d}" for s in seeds], rotation=90, fontsize=7)
    ax.set_ylabel("load lost (MW)")
    ax.set_title("Per-incident outcome, hardest first", fontsize=11)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES / "per_scenario.png", dpi=150)
    print("wrote", FIGURES / "per_scenario.png")


def table(summaries: dict):
    specs = order(summaries)
    rows = []
    for s in specs:
        d = summaries[s]
        c = d.get("counters", {})
        rows.append(
            f"| {LABELS.get(s, s)} | {d['mean_load_lost_mw']:.0f} | "
            f"{d['max_load_lost_mw']:.0f} | {d['pct_of_no_action_loss_avoided']:+.0f}% | "
            f"{d['scenarios_fully_contained']} | {d['scenarios_made_worse']} | "
            f"{d['benign_scenarios_damaged']} | "
            f"{c.get('invalid_actions', 0) if c else '—'} |"
        )
    header = ("| policy | mean MW lost | worst MW | damage avoided | contained | "
              "made worse | benign incidents damaged | invalid actions |\n"
              "|---|---|---|---|---|---|---|---|")
    out = header + "\n" + "\n".join(rows)
    (RESULTS / "summary_table.md").write_text(out + "\n")
    print("\n" + out)


if __name__ == "__main__":
    summaries, per_seed = collect()
    bars(summaries)
    per_scenario(per_seed)
    table(summaries)
