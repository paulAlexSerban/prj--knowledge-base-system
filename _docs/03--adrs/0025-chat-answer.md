# ADR 0025: The chat page writes the answer, the gateway does not

Status: decided during the build.

## Context

The gateway returns cited chunks or a graph path. Someone still has to turn that into a sentence for the demo. That step must not gain store credentials, and it must not treat retrieved text as instructions.

## Decision

`frontend/` is a Streamlit page, its own image, and it calls the REST API only. When `OPENROUTER_API_KEY` is set, it sends the question and the cited snippets to OpenRouter through LangChain (`ChatOpenAI`, base URL `https://openrouter.ai/api/v1`). The model name is `OPENROUTER_MODEL`. Snippets are wrapped as untrusted content. If the key is missing or the call fails, the page shows the cited snippets. It has no tools and no store credentials. Notebooks `50` and `51` are two read-only LangChain agents over the MCP server, one for the policy question and one for `roadmap_deals`. They are not a deployed service. LangGraph is not used.

## Alternatives rejected

- The gateway calling the model and returning a finished answer. The access layer would grow a prompt, a key, and a failure mode that is not retrieval. Callers that only want chunks would pay for it.
- LangGraph for the chat turn. One fixed prompt over cited snippets is not a graph of nodes. A graph would not change permissions, retrieval, or the fallback.
- A local chat model in the image. Not evaluated. The offline path is the cited snippets, which is what the demo can defend without a key.

## Consequences

- Answer quality depends on a third-party model and on the caller picking the right tool. The gateway does not correct a wrong pick.
- The page has no `/metrics` endpoint and is not scraped. It joins `kbs-obs` only to export traces. The chat span is the parent of `POST /v1/search`.
