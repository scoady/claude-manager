# Live File Tree

## Summary
A real-time file explorer panel showing which files agents are reading, editing, and creating — with diffs viewable inline.

## Motivation
When agents work autonomously, it's hard to know what they're actually changing. A live file tree gives instant visibility into the project's state and what's being touched.

## Features
- **File tree sidebar**: collapsible tree view of the project directory
- **Live highlights**: files pulse/highlight when an agent reads or edits them
  - Blue glow = currently being read
  - Yellow glow = being edited
  - Green = newly created this session
- **Inline diffs**: click a modified file to see a diff view (before/after)
- **Agent attribution**: hover shows which agent(s) touched the file and when
- **Gitignore-aware**: respects .gitignore to hide noise (node_modules, etc.)
- **Auto-scroll**: tree auto-expands to show the file currently being worked on

## Data Source
Tool events (`tool_start`, `tool_done`) already include tool name and input. For `Read`, `Edit`, `Write`, `Glob` tools, extract the file path from the input and broadcast a `file_activity` WS event.

## UI Sketch
- Left sidebar panel (toggleable), similar to VS Code's explorer
- Tree nodes show: filename, last-modified timestamp, agent badge
- Click file → split view with syntax-highlighted diff
- Filter bar at top to search files
