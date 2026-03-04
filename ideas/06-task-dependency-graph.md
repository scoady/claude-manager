# Task Dependency Graph & Auto-Scheduling

## Summary
Visual DAG (directed acyclic graph) of task dependencies with automatic scheduling — tasks start automatically when their dependencies are satisfied.

## Motivation
Complex projects have tasks with dependencies (e.g. "implement API" must finish before "write integration tests"). Currently tasks are flat lists. A dependency graph lets the controller intelligently sequence work and parallelize independent tasks.

## Features
- **Dependency declarations**: tasks can declare `depends_on` relationships in TASKS.md:
  ```markdown
  - [ ] Implement user API  #task-1
  - [ ] Implement auth middleware  #task-2
  - [ ] Write API integration tests  #task-3  (depends: #task-1, #task-2)
  - [ ] Write frontend components  #task-4  (depends: #task-1)
  ```
- **Visual DAG**: interactive graph view showing tasks as nodes, dependencies as edges
  - Color-coded: green (done), yellow (in-progress), gray (blocked), blue (ready)
  - Click node to see task details, start task, or view agent output
- **Auto-scheduling**: when a task completes, automatically start the next ready tasks
  - Respects parallelism limits from project config
  - Controller agent receives "next batch" of ready tasks
- **Critical path**: highlight the longest dependency chain to show what's blocking completion
- **Cycle detection**: warn if dependencies form a cycle

## Implementation Notes
- Extend TASKS.md parser to understand `(depends: #id, #id)` syntax
- New `task_graph` API endpoint returning nodes + edges
- Frontend: use a lightweight DAG layout library (e.g. dagre) or custom CSS grid layout
- Controller prompt updated: "Check the dependency graph. Start all tasks whose dependencies are satisfied."

## UI Sketch
- New "Graph" view toggle in the Tasks tab (alongside the current list view)
- Horizontal left-to-right layout: independent tasks on the left, dependent tasks flow right
- Minimap for large graphs
- Drag to rearrange, click to drill in
