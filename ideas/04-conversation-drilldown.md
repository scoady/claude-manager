# Agent Conversation Drilldown

## Summary
Full conversation viewer for any agent session — see the complete back-and-forth between the agent and Claude, including thinking blocks, tool calls with inputs/outputs, and the assistant's reasoning.

## Motivation
The feed shows milestones and stream chunks, but sometimes you need to see the full conversation to understand why an agent made a particular decision or to debug unexpected behavior.

## Features
- **Conversation timeline**: chronological view of all messages (system, user, assistant)
- **Thinking blocks**: expandable sections showing Claude's extended thinking (if available)
- **Tool call detail**: each tool call shown as a card with:
  - Tool name and timing
  - Input (syntax-highlighted, collapsible for large inputs)
  - Output (syntax-highlighted, collapsible)
  - Success/error status
- **Search**: full-text search across the conversation
- **Jump-to-milestone**: click a milestone in the feed to scroll to that point in the conversation
- **Live mode**: conversation updates in real-time as the agent works
- **Export**: download conversation as JSON or formatted markdown

## Data Source
The backend already persists JSONL session files via Claude CLI. These contain the full conversation. Add a new API endpoint to read and parse these files.

## API
- `GET /api/agents/{session_id}/conversation` — returns parsed conversation messages
- Optional query params: `?after_uuid=xxx` for live polling, `?search=term` for filtering

## UI
- Full-page or slide-out panel view
- Click agent card → opens drilldown
- Tabbed: "Summary" (current feed view) | "Conversation" (full detail)
