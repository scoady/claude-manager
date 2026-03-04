# Project Templates & Quick Start

## Summary
Pre-built project templates for common use cases — create a new project from a template with pre-configured goals, tasks, skills, and settings.

## Motivation
Setting up a new managed project (PROJECT.md, TASKS.md, skills, permissions) is repetitive. Templates let users start fast with best-practice configurations for common scenarios.

## Features
- **Built-in templates**: ship with templates for common use cases:
  - "Web App" — full-stack app with frontend + API + tests
  - "CLI Tool" — command-line utility with arg parsing + tests
  - "Library" — reusable package with docs + publishing
  - "Bug Fix" — focused debugging session with repro steps
  - "Code Review" — audit existing code for issues
  - "Migration" — upgrade dependencies, refactor patterns
- **Custom templates**: save any existing project as a template
- **Template contents**: each template includes:
  - PROJECT.md skeleton with section prompts
  - Pre-populated TASKS.md with common task patterns
  - Recommended skills (auto-enabled)
  - Settings preset (model, parallelism, approval gates)
- **Template gallery**: browsable UI with descriptions and previews

## UI
- "New Project" modal gets a "From Template" tab
- Template cards with icon, name, description, and "Use" button
- After selecting: pre-fills the create form, user can customize before creating

## Storage
Templates stored in `~/.claude/templates/` as directories mirroring the project structure.
