# ADR 0026: Rules classify the question, and a model may only break a tie

Status: accepted. Built in `gateway_core.router`.

## Context

Spike 02 splits questions into semantic, relational, and hybrid. Similarity search answers the policy question and fails the roadmap question. The graph answers the roadmap question and fails on prose. Until this decision, the caller picked the tool: the chat page sent one button to `roadmap_deals` and every typed question to search. Notebooks let the model pick a tool, which is the same choice with no allow-list.

The router can only reach what already exists. `find_related` has four templates. `roadmap_deals` is the only relational question with parameters (quarter, minimum ARR). It is not a general multi-hop planner.

## Decision

`POST /v1/ask` and the MCP tool `ask_knowledge` classify the question, then call the same `search` or `find_related` the explicit routes call. Authorization is unchanged. A wrong route can return nothing. It cannot read another principal's rows, and the model text is never a permission and never Cypher.

Rules run first, in code:

| Pattern                                                                         | Route                                                                                                                                                                       |
| ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A deal, ARR, or roadmap, together with an epic, "depend", or "unreleased"       | `roadmap_deals`. Explanatory wording (`explain`, `spec`, `what does`) makes it hybrid: the graph path, then hybrid search restricted to the deal and epic ids on that path. |
| "teammates" or "team members", and an id such as `emp-1`                        | That template. Without an id, semantic search. The template requires an id, and guessing one would 400.                                                                     |
| "which policies" or "list policies", and a phrase about what the caller may see | The policies template. A question about a policy's content stays semantic, including one that says "depend".                                                                |
| One of deal, epic, ARR, roadmap, or unreleased, without the pair above          | Semantic, low confidence.                                                                                                                                                   |
| Anything else                                                                   | Semantic, high confidence.                                                                                                                                                  |

A bare `Q3` means `2026-Q3`, the demo year in `graph_read.DEFAULT_QUARTER`. A year in the question is kept. An ARR number, including a `k` or `m` suffix, becomes `min_arr`. The graph lookup uses a limit of 25 so the snippet limit does not truncate question 2.

A model is called only when the rules are low confidence, `OPENROUTER_API_KEY` is set, and `OPENROUTER_ROUTER_MODEL` is set. The reply must be JSON whose `route` is `semantic`, `relational`, or `hybrid`, and whose `graph` is one of the four templates. Hybrid is accepted only for `roadmap_deals`. `teammates` and `team` need an id. A timeout (2 seconds), a transport error, or any other label keeps the rules result. Tests and `make eval` set no key, so they use the rules only. High-confidence questions never call the model, even when a key is set.

`/v1/search` and `/v1/graph/related` stay as explicit, unrouted endpoints. The chat page sends buttons and typed questions to `/v1/ask`.

## Alternatives rejected

- A model as the only classifier. It would route the parental-leave question to the graph on a bad day, and the eval could not run without a key. The rules are the authority. The model breaks ties.
- Letting the model emit Cypher or pick an arbitrary tool name. That is the failure the templates exist to prevent.
- Routing every mention of "policy" to the policies template. "What is our parental leave policy?" is a prose question. The template lists policies. It does not answer them.
- Dropping hybrid. Spike 02 asks for a path plus the prose of those entities. The object-id filter is one optional argument on search. An empty path does not fall through to a corpus-wide search.

## Consequences

- The router is a closed set of templates. A new relational question needs a template and a rule, not a prompt edit.
- Low-confidence questions with a key spend a model call before retrieval. That call is outside the 250 ms budget and is why high-confidence questions skip it. Eval does not pay it.
- `kbs_router_decisions_total{route,source}` counts the class and whether the rules or the model decided. It does not label the question or the principal.
- Notebooks `50` and `51` still let the model pick among the MCP tools, including `ask_knowledge`. The chat page does not.
