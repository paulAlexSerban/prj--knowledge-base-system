# Chat page

Streamlit process. Image `chat-ui`, Compose service `chat-ui`, host port 8501. It calls the knowledge API and holds no store credentials.

Personas, from `CHAT_PERSONAS`: `employee_eng`, `hr_admin`, `outsider`, `product_lead`. The sidebar groups demo questions by category. A question sets the persona, then calls `POST /v1/ask`. The caption names the route the gateway chose, the answer mode, the request id, and the trace id. The trace id opens Grafana Explore on Tempo. Changing persona clears the chat.

The questions cover a policy, teammates, a team, the policies the caller may see, the high-ARR roadmap question, the same question phrased as an explanation (hybrid), that roadmap question as an outsider and as an engineer, a question with no answer, parental leave with no country, the checkout spec, and the checkout-retry epic. The three original keys (`parental-leave`, `roadmap`, `roadmap-outsider`) are unchanged.

A graph or hybrid route draws the path with `st.graphviz_chart` and lists each hop. A semantic route does not. The path text is untrusted data, the same way a snippet is. Chunks are shown when the route searched. A path is also shown as a numbered source when the route did not search.

With `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` set, a LangChain chain writes the answer: a prompt, `ChatOpenAI` against OpenRouter, and a string parser. A missing key or a failed call falls back to the passages or the path table. No model call runs when the key is missing. The chain has no tools.

Metrics listen on port 9105, which is not published on the host. Alloy scrapes `chat-ui:9105`. The series are `kbs_chat_turns_total`, `kbs_chat_turn_seconds`, `kbs_chat_gateway_seconds`, `kbs_chat_llm_seconds`, `kbs_chat_llm_tokens_total`, and `kbs_chat_graph_hops`. Definitions are in [observability/metrics.md](../observability/metrics.md). Logs are JSON on stdout, with `trace_id` and `span_id` on the line.

Traces go to Alloy when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. One question is one trace: `chat.turn`, then the gateway call, `POST /v1/ask`, `router.classify`, retrieval, and the store spans. Ingestion of an object is a different trace. The connector starts it, and the worker continues it from the SQS `traceparent`. In Grafana Explore, `{ span.source_id = "hr" }` finds that trace. The chat trace is the one whose root is `chat.turn`.
