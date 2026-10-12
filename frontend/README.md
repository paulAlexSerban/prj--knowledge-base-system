# Chat page

Streamlit process. Image `chat-ui`, Compose service `chat-ui`, host port 8501. It calls the knowledge API and holds no store credentials.

Personas, from `CHAT_PERSONAS`: `employee_eng`, `hr_admin`, `outsider`, `product_lead`. The sidebar has three demo buttons. Parental leave in Germany uses `employee_eng` and `POST /v1/search`. The high-ARR Q3 question uses `product_lead` and `POST /v1/graph/related` with kind `roadmap_deals`. The same question as `outsider` is the empty result. A button selects the persona, then asks. Changing persona clears the chat. A typed question always uses search.

A path is rendered as a numbered source: deal, epic, team, and the deal and epic versions. The path text is untrusted data, the same way a snippet is. With `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` set, LangChain writes the answer. Without a key, search prints the passages and `roadmap_deals` prints the path table. No model call runs in that case.

There is no `/metrics` endpoint. Traces go to Alloy when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. The page sends `traceparent` on the gateway call.
