# Project Brief

## Context
A global company has its operational knowledge spread across many systems, owned by different departments across the globe and stored in very different shapes. Each department (Sales, Product, Engineering, Finance, People, etc) owns its own sources, which are updated continuously and independently: records are created, edited, or deleted every day, with no central coordination and no common schema. 

The company wants to build AI agents that automate process workflows across departments. Some examples: 
- A chatbot that lets employees query HR information (policies, procedures, benefits) in natural language. 
- An agent that connects information from Sales, Engineering and Product to produce data-driven roadmaps. 

None of these agents exist yet. To build them reliably, agents need a way to access knowledge that is accurate, current and authorized at the moment they act. Today there is no system that provides this.

![System Context](00-c4-l1--system-context.png)

## Requirements

- System should be able to handle data from distributed sources belonging to different departments, with different formats, access methods and ownership.
- System should transform that data into knowledge that AI agents can easily interpret and use.
- System should expose the knowledge to AI agents through a consistent access layer. 
- System should maintain itself over time, reflecting additions, modifications and deletions in the source systems without manual intervention.