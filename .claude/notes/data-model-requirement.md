# Data Model Requirement for Language Analysis

IMPORTANT: The LANGUAGE-ANALYSIS.md must include a dedicated section on data model capabilities per language.

Key questions to answer:
- Which languages have the best type systems for expressing agent state machines? (sum types, pattern matching, generics)
- Which have the strongest serialization story? (Rust's serde, Python's Pydantic, Go's struct tags)
- Which concurrency primitives naturally map to our data flow? (channels, actors, async streams)
- How does each language handle typed event streams for event sourcing?
- What are the best event store / CQRS libraries per language?
- How does ownership/borrowing (Rust) vs GC (Go/Python) affect shared state in the agent broker?

A separate DATA-MODEL-ANALYSIS.md is being written concurrently — cross-reference it in the language analysis.
