# Exposing Knowledge to AI Agents Though Consistent Access Layer

## Context
- a person searching a wiki of 2000 documents can skim several results, notice that a page is three yeas old, aks a colleague in a hallway, and weigh the answer agains what they already know
- LLM models and AI agents change this relationship - read quickly, not naturaly judge provenanve, increasingly act on what thye retrive
- knowledge is the same, consumer is different, and the difference express weakness
- the question is how to connect models in a consistent, secure, and maintainable way
- the usual starting point is a script that embeds documents into a vector database and pastes the top results into a prompt
- authorization is te mst consequential design decision, there are two broad strategies:
  - pre-filtering - pushess access predicates int the index query itself, so the index only considers documents the prinicplal could see - this preerves recall under restrictive permissions, but requires an index that suppports efficient filtering on the relevant attributes
  - post-filtering - retrives candidates forst and then checks each one, which is simpler and works with any index that supports efficient filtering on the relevant attributes - simpler but can leave a user with very few results when most candidates are hidden
  - many production system scombine them:
    - a coarse tenants and a group filter is applied in the index
    - fine grained checks run afterward
    - row level security in PostgreSQL applies the same idea at the databas elayer, attaching predicates that the engine enforces on every query regardless of how the query was written
- consistency scross consumers comes from contracts, and the contracts should be written down in machine-checkable form
- tool description deserves the same care as schemas, becuase for an agent the description is the interface it uses to decide when and how to call a tool
- MCP standard defines client-server model in which servers expose capabilities such as tools, resources, and prompts - benefist it provides: you implement search and fetch once as an MCP server, and every MCP capable client receives the ssame semantics without custom integration work
  - it does not solve authorizaiotion by itself - an MCP server has to authenticate the user, enforce access rules on every operation, and handle credentials responsibly

## Trade-offs and pitfalls
- post-filter starvation - search pipeline retrieves the top ten candidates and then removes the ones the user cannot see - for most users in a permissive environment this works fine, but for a user restricted to a small project space, the same pipeline returns almost nothing, and the chatbot with "I could not find any information", which is false
  - FIX: combination of pre-filtering on corarse attributes, larger candidate sets, and monitoring of results per principal segment - measure empty-result rate by permision group, not just globally, because the global average hides the users who are most affected
- permission staleness - access rules lives in source systems, sync pipelines copies them into the knwoledge layer on some schedule - during the gap between a permission change and it's propagation, the layer may expose content that should no longer be visible, or deny content that shold be
  - for sensitive material - safe option is to check permissions agains source system or a frequently updated permission store at read time, accepting latency cost
  - for lower sensistivity material, a bounded sync window with clear documents may be acceptable - the important part is making thea decision explicitly and writing it down, rather than discovering the window during an incident

## Practices
- build evaluation set before you tune anything - should contain real queries drawn from consumers, eahc paired with the documents that should answer it
- make the layer the only path to data - consumers shoudl nbot hold credentials for the underlying index or document store - enforce though network policy and scoped service credentials, so that a chatbot cannot bypass authorization by querying the index directly - scope credentials per consumer, which als makes it possible to revoke one integration without distrupting the others and the attribute usage in logs
- version and deprecate deliberately - version the pulbic API, record the embeding model and chunking configuration in index metadata, andmaintain a changelog that consumers can read - uhen a breaking change is unavoidable, run old and new version in parallel with dual writes during migration, and set a date for retirement that is communicated well i advance
- treat retrived content as data, never as instructions - delimit retrived text clearly in prompts, state explicitly that instructions inside retrived documents must not be followed, and never allow retrived content to modify tool permissions ot trigger write-capable actions without an explicit confim=rmation step from the user - sanitize rendered output, particularrly links and markup, since a model can n=be induced to produce content that a downstream renderer interprets - return documetn identifiers with every answer so that citations are verifiable, and make the source of eah clain inspectable by the user - a system whose answer can be traced to specific documents is far easier to trust and debug thabn one  tha prodices fluent text with no provenence