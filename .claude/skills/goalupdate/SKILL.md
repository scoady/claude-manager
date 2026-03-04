---
name: goalupdate
description: Read the codebase and update GOALS.md with current project goals, completed milestones, and upcoming work.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Update GOALS.md

Analyze the current state of the claude-manager project and update `GOALS.md` at the repo root with an accurate summary of goals, milestones, and upcoming work.

## Steps

### 1. Gather current state

Read these files to understand what exists today:

```
GOALS.md                          — current goals file (if it exists)
README.md                         — project overview
SCOPE.md                          — project scope and architecture
backend/main.py                   — backend endpoints and features
backend/models.py                 — data models
frontend/js/app.js                — frontend state and features
frontend/js/feed/FeedController.js — feed UI features
frontend/js/feed/AgentSection.js  — agent card features
```

Also run:
```bash
git log --oneline -30              # recent commits for milestones
git tag -l                         # released versions
ls .claude/skills/*/SKILL.md       # available skills
```

### 2. Identify changes since last update

Compare what's in the current `GOALS.md` against the actual codebase:
- New features that were implemented (move from "Upcoming" to "Completed")
- New goals discovered from recent work
- Goals that are no longer relevant
- Milestone dates from git log

### 3. Update GOALS.md

Use the Edit tool to update the file in place. Preserve the existing structure:

```markdown
# Claude Manager — Goals

## Vision
(1 paragraph — what this project is and why it exists)

## Core Goals
(numbered sections: Orchestration, Visibility, Project Management, Skills, Workflow Integration, Automation)

## Completed Milestones
(table: Date | Milestone — newest at bottom, dates from git log)

## Upcoming Goals
(### Short-term, ### Medium-term, ### Long-term — checkbox lists)

## Non-Goals
(bullet list of what this project is NOT)
```

### 4. Verify

- Ensure all completed features from git log appear in the milestones table
- Ensure "Upcoming" items that are now implemented get moved to "Completed"
- Ensure no stale or duplicate entries
- Keep the file concise — each milestone is one line, each goal is one bullet

### Output

Report what changed:
- Milestones added
- Goals moved from upcoming to completed
- New goals added
- Goals removed
