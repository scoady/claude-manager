# Approval Gates & Human-in-the-Loop

## Summary
Configurable breakpoints where agents pause and wait for human approval before proceeding with high-impact actions.

## Motivation
Fully autonomous agents are powerful but risky. Some actions (git push, file deletion, external API calls, deployment triggers) should require explicit human sign-off.

## Features
- **Gate rules**: define patterns that trigger a pause (e.g. tool name matches `Bash` + command contains `push`, `rm -rf`, `curl -X POST`)
- **Approval UI**: when a gate fires, the agent card shows a prominent approval prompt with:
  - What the agent wants to do (tool name, input preview)
  - Approve / Deny / Edit-and-approve buttons
  - Optional: "Always allow this" to whitelist the pattern
- **Timeout**: configurable auto-deny after N minutes of no response
- **Audit log**: all gate decisions (approved/denied/timed-out) logged with timestamp and context

## Configuration
Per-project in `manager.json`:
```json
{
  "approval_gates": [
    { "tool": "Bash", "pattern": "push|deploy|rm -rf", "action": "pause" },
    { "tool": "Write", "pattern": "*.env|*.key|*.pem", "action": "pause" }
  ]
}
```

## Implementation Notes
- Hook into `AgentSession` tool event processing — when a tool_use matches a gate, set phase to `awaiting_approval` and pause stream consumption
- New WS event `approval_required` with tool details
- New API endpoint `POST /api/agents/{session_id}/approve` with approve/deny payload
- On approve: resume stream processing; on deny: inject a "The user denied this action" message
