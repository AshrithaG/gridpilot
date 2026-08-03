| policy | mean MW lost | worst MW | damage avoided | contained | made worse | benign incidents damaged | invalid actions |
|---|---|---|---|---|---|---|---|
| do nothing | 117 | 512 | +0% | 0 | 0 | 0 | 0 |
| greedy load shedding | 194 | 716 | -65% | 0 | 11 | 2 | 0 |
| greedy, what-if checked | 120 | 512 | -3% | 0 | 1 | 0 | 0 |
| redispatch heuristic | 85 | 213 | +27% | 3 | 1 | 0 | 0 |
| LLM agent | 80 | 212 | +32% | 3 | 1 | 0 | 3 |
| agent, no what-if guardrail | 93 | 275 | +21% | 3 | 3 | 0 | 2 |
| agent, no sensitivity tool | 102 | 281 | +13% | 0 | 2 | 0 | 5 |
