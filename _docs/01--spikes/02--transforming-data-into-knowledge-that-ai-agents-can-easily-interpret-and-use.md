# Transforming Data into Knowledge that AI Agents Can Easily Interpret and Use

## Context

- data is sourced from a variety of sources, in a variety of formats and with a varaiety of structures and connections
- RAG is the most widely adopted pattern
- Knowledge graphs are widely discussed to complement RAG
- failure modes:
  - lack the info entierly because the document was never ingested or was ingested too kate
  - can have the information but fail to retrieve it, because the query and passage use different vocabulary or because the relvant fact is buried in a long document
  - can retrieve the right passage but misinterpret it, because the passage refers to "the new policy" without saying which policy or when it took effect
  - it can answer from information the requesting user is not authorized to use - security problem, not a quality problem
- where decisions are needed to tackle the listed failure modes:
  - missing data is an ingestion problem
  - retrival failures are an indexing and query-understanding problem
  - misinteprepretation is a problem of context: metadata, versioningm and relathionship between entities
  - authorization failures are an access control problem that needs to be enforced at retrival time, not hoped for the prompt
- what kind of questions user migt ask:
    - semantic and open ended - well suited to similarity search over text: "what have we said about data residency in enterprise contracts?"
    - relational and precise - poorly serverd by similarity search, because the anser depends on joining facts across entities and applying filters: "which customers on the enterprise plan are using the deprecated export API and have renewal dates in Q4?"

## Technical explanations
### RAG (Retrieval-Augmented Generation)
- documents are split into chunks, each chunk is converted into an embeding vector, vectors are stored in an index, and at query time the query is embeded and the nearest chunks are retrieved and placed in the model's context window
- knwoledge lives outside the model, so it can be updated without retraining, it can be attributed to sources, and access control can be applied to the retrieved step
- decisions that determine quality
  - chunking strategy: how to split the document into chunks? @TODO: adr on chunking strategies, approaches and patterns
    - chunks that are too small lose context needed to interpret them
    - chunks that are too large dilute the embeding and waste context budget
    - splitting on fixed token counts often cuts across text (sentences, tables, code blocks, etc.)
    - structure aware chunking, which respects headings and document hierarchy, usually performs better, and eahc chunk should cary metaata such as source, section path, version, timestamp, and access labels
  - embeding model: how to capture semantic similarity? useful for paraphrased queries but can also retrieve passafes that are topically clise and facually irrelevant
    - dense retrival is weak on exat identifiers such as error codes, product SKUs, and names, where lexical mathcing is more reliable
      - this is the main reason hybrid retrival, which combines dense vectors with saparse keyword scoring such as BM25 has became a default in serious systems @TODO: adr on keyword scoring systems
      - reciprocal rank fusion is a simple and robust wa to merge the two ranked lists without needing to calibrate scores agains each other
  - retival quality approaches: how to ensure the retrived passages are relevant to the query?
    - reranker - cross-encoder model that scores each query-passage pair more accurately than bi-encoder used for initial retrival at high cost
    - a tipical pipeline retrieves cheaply, reranks them, and passes the top handful to the generator
    - query transformation such as rewriting a conversational follow-up into a standalone query or generating multiple paraphrases, can also recover recall that a single embeding misses

### Knowledge Graphs
- represents information as entities and typed relationships, typically expressed as SUBJECT-PREDICATE-OBJECT triples
- key property - relationships are first-class and can be traversed, filtered, and aggregated with precision
- the strength of a knowledge graph is exactness and explainability
- graphs aslo supports multi-hop reasoning naturally - the path from a support ticket to a product component to an owning team is a sequence of edges, and the system can show that path as justification
- entity resolution, is central to its value, and it is often the hardest part of building the graph in the first place
- graphs require schema design and extraction - someone must decide which entity types and relationships types mattern, and an extraction pipeline must populate them from unstructured text, typically using a combination of rules, classical NLP, and langiage models
- LLM-based extraction has made this much mode practical, but it introduces it's own error profile:
  - extracted triples can be plasible and wrong, and errors compound when graph is used for reasoning
- graphs also strugglw with the open-ended, prose heavy knwoledge that makes up the most of an organization's documenttaion - representing the nuance of a design rationale as tripples loses much of what made it useful

### Hybrid Knowledge Representation
- productive systems do not choose - knwoledge is stored in multiple representations and route queries to the representation best suited to them - or combine evidence from several
- common:
  - keep raw documents in object storage with rich metadata in a relational or document store
  - maintain a vector index over chunks for semantic retrival
  - extracts a knowledge graph fo ke entities and relationship that links chunks to the entities they mention
- retival can start from  vector search, expand though the graph to related entities, and pull linked pasages
- the pattern is sometimes called GraphRAG
- the cost is operational complexity - you have multiple indexes that must be kept consistent with the source of truth, and a bug in one can produce answers that look authoritative but are assembled from stale or mismatched pieces
- TIP: to reason about the design - ask for eahc category of question: "which representation should be the autority"
  - factual lookups on structured attributes - the relational graph store should be  authorative and the model should be told to trust it
  - explanatory questions about reasoning or history - the document text should be authorative and the graph should only help locate it
  - making these authority rules explicit in code, rather than leaving them to the model's discretion, is one of the more important habits to develop

### Alternatives
- for small, relatively static corpus, such as product manual or a set of policy documents ythat fit comfortably within a mode's context window, the simplest effective design may be to place the docuemnts directly in the prmpt, possibly with prompt caching to control cost
- fine-tunning is recommended for chaning behavior, style, or output format
- a well-designed relational schema with full-text search, queried through tool calling agents, anc answer a surprising share of organizational questions, especially when the data is already structured - in many organizations the best first version is an agent with a set of well-described tools over existing systems of record, with retrival added when those tools fall short
- the principle is to use the least complex representation that answers your actual questions

## Industry practices
- start with the question, not the technology - collect the questions the system must answer, classify them by type, and decide which representation is authoritive for each class - this helps prevent architectural mistakes - keep the classificaiton in code or configuration so that routing decisions are explicit and testable
- treat the document as the unit of truth and everythin else as a derived index
- make provenence a first-class feature - every claim in a generated answer should be traceable to a source passage with a verion and timestamp
- evaluate continously - manitain a regression suite of labled questions, run it on every change to chunking, embedding models, prompts, or retrival parameters, and track rerival and answer metrics separately
  - include adversial cases - questions whise answers are not in the corpus, questions with conflicting sources, questions that probe access boundries
- enforce quthorization in the data layer and test it directly - write tests that create users with different group memeberships and assets that retrival, explanantion, and tool calls return only permitted content - treat these tests as security tests, with the same review rigor as any other access control code
- prefer the simplest representation that meets the requirement
  - add a knowledge graph when you have demonstrated that multi-hop relational questions matter and that vector retrival fails on them
  - add a reranker when recall is good but precision is poor
  - each addition should be justified by a measured gap, not by expectation that a more sophisticated architecture will perform better