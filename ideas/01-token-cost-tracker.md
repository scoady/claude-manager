# Token & Cost Tracker

## Summary
Real-time and historical token usage + estimated cost tracking per agent, per project, and globally.

## Motivation
Running multiple autonomous agents burns through tokens fast. Without visibility into spend, it's easy to blow budgets or miss runaway agents that are spinning in loops.

## Features
- **Per-agent counters**: input tokens, output tokens, cache reads/writes — updated live from stream events
- **Cost estimation**: multiply token counts by per-model pricing (configurable in settings)
- **Project-level rollup**: aggregate cost across all agents in a project session
- **Global dashboard widget**: total spend across all projects, with a sparkline chart
- **Budget alerts**: optional threshold per project — agent gets paused or warned when nearing limit
- **Historical log**: persist token counts to SQLite/JSON so users can see daily/weekly trends

## Data Source
The `stream-json` output includes `usage` blocks on each assistant turn with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`. These are already flowing through `AgentSession._process_event()`.

## UI Sketch
- Small token counter badge on each agent card header (e.g. "12.4k tokens · ~$0.08")
- Project header shows aggregate (e.g. "Total: 142k tokens · ~$1.20")
- Settings page: model pricing table (editable), budget thresholds
- Optional: cost history chart in a new "Analytics" tab
