import json

from gridpilot.agent import AgentOperator
from gridpilot.cascade import CascadeSim
from gridpilot.grid import load_case
from gridpilot.llm import Scripted, parse_tool_call


def scripted_agent(replies, **kw):
    return AgentOperator(_provider=Scripted(replies), **kw)


def overloaded_sim():
    """A grid with a live overload, so the operator has something to react to."""
    sim = CascadeSim(load_case())
    hottest = int(sim.net.line.index[int(sim.loadings().argmax())])
    sim.net.line.at[hottest, "max_i_ka"] *= 0.75
    sim.solve()
    assert (sim.loadings() > 100).any()
    return sim


def test_parses_bare_fenced_and_trailing_json():
    assert parse_tool_call('{"tool":"done","args":{}}').tool == "done"
    fenced = '```json\n{"tool":"what_if","args":{"actions":[]}}\n```'
    assert parse_tool_call(fenced).tool == "what_if"
    assert parse_tool_call('I will look. {"tool":"get_grid_state","args":{}}').tool == \
        "get_grid_state"
    assert parse_tool_call("no json at all").tool is None


def test_unparseable_reply_is_retried_then_counted():
    agent = scripted_agent(["I think we should shed some load.", '{"tool":"done","args":{}}'])
    agent(overloaded_sim())
    assert agent.stats["unparseable"] == 1
    assert agent.stats["turns"] == 2


def test_guardrail_blocks_a_plan_that_makes_things_worse():
    sim = overloaded_sim()
    bus = int(sim.net.load.sort_values("p_mw", ascending=False).iloc[0].bus)
    # shedding a big block far from the overload should not beat doing nothing
    plan = {"tool": "apply_actions",
            "args": {"actions": [{"type": "shed_load", "bus": bus, "mw": 100}]}}
    agent = scripted_agent([json.dumps(plan), '{"tool":"done","args":{}}'],
                           require_what_if=True)
    before = sim.metrics().load_served_mw
    agent(sim)
    assert agent.stats["guardrail_rejections"] == 1
    assert sim.metrics().load_served_mw == before  # nothing was committed


def test_without_the_guardrail_the_same_plan_goes_through():
    sim = overloaded_sim()
    bus = int(sim.net.load.sort_values("p_mw", ascending=False).iloc[0].bus)
    plan = {"tool": "apply_actions",
            "args": {"actions": [{"type": "shed_load", "bus": bus, "mw": 100}]}}
    agent = scripted_agent([json.dumps(plan), '{"tool":"done","args":{}}'],
                           require_what_if=False)
    before = sim.metrics().load_served_mw
    agent(sim)
    assert agent.stats["guardrail_rejections"] == 0
    assert sim.metrics().load_served_mw < before


def test_malformed_action_is_reported_with_the_correct_shape():
    sim = overloaded_sim()
    bad = {"tool": "apply_actions",
           "args": {"actions": [{"type": "redispatch", "gen": 3, "new_mw": 100}]}}
    agent = scripted_agent([json.dumps(bad), '{"tool":"done","args":{}}'])
    agent(sim)
    assert agent.stats["invalid_actions"] >= 1
    hint = next(t.result for t in agent.transcript if t.tool == "apply_actions")
    assert "'mw'" in hint and "not a delta" in hint


def test_unknown_tool_gets_the_tool_list_back():
    agent = scripted_agent(['{"tool":"summon_more_power","args":{}}',
                            '{"tool":"done","args":{}}'])
    agent(overloaded_sim())
    hint = agent.transcript[0].result
    assert "relief_options" in hint and "what_if" in hint


def test_sensitivity_tool_can_be_withheld_for_the_ablation():
    agent = scripted_agent(['{"tool":"relief_options","args":{"line":5}}',
                            '{"tool":"done","args":{}}'],
                           give_sensitivities=False)
    agent(overloaded_sim())
    assert "not available" in agent.transcript[0].result
