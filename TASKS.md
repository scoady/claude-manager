# Tasks

# TASKS.md — Claude Manager v1.7.0

## Completed (v1.6.0)
- [x] Role Manager backend — RolePreset with persona/expertise, CRUD service, merge with template built-ins
- [x] Role Manager frontend — Settings Roles tab, card grid, create/edit/delete, role picker in WorkflowPanel
- [x] Artifacts Tab backend — file browsing, read_file, get_git_status, .gitignore respect, path traversal protection
- [x] Artifacts Tab frontend — split-pane file tree + preview, syntax highlighting, git status badges

---

## Sprint 1 — Canvas Foundation ✅
- [x] Build the Canvas Engine (`frontend/js/canvas/`) — `CanvasEngine.js`, `WidgetFrame.js`, `WidgetRegistry.js`; Shadow DOM widget renderer, CSS Grid placement, batched entrance animations, WS event routing `@engineer-1`
- [x] Build the Canvas REST API (backend) — `WidgetState`/`WidgetCreate`/`WidgetUpdate` models, `CanvasService` with JSON persistence, 5 REST endpoints, WebSocket broadcast `@engineer-2`
- [x] Build the MCP Server stub (`backend/mcp/canvas_server.py`) — FastMCP on port 4041, `canvas_put`, `canvas_remove`, `canvas_list` tools `@engineer-2`
- [x] Canvas view toggle in header — Dashboard vs Canvas tab, empty-state constellation background `@engineer-1`
- [x] UX mockups — v8a widget tiles (stat/chart/log), v8b canvas toolbar + drag UX, v8c prompt builder drawer, v8d widget header spec `@designer-1`

---

## Sprint 2 — Agent Integration + Constellation ✅
- [x] MCP tools: `canvas_animate`, `canvas_scene` (bulk replace), `canvas_clear` + CSP docstring warnings `@engineer-2`
- [x] Wire MCP into agent spawn config — `mcp_config.py` + `--mcp-config` injection stub with `canvas_enabled` flag `@engineer-2`
- [x] Implement Prompt Builder panel per v8c spec — chip selectors (Focus/Style/Layout), free text, Redraw → `POST /api/canvas/{project}/prompt` `@engineer-1`
- [x] Implement Constellation scene generator — tasks as stars clustered by status, SVG connection lines, pulse/glow animations `@engineer-1`
- [x] Agent prompt template for constellation `canvas_scene` call — `backend/templates/constellation-prompt.md` `@engineer-2`
- [x] Widget header bar per v8d spec — title, agent attribution badge, relative timestamp, hover-reveal × remove `@engineer-1`
- [x] Drag-to-reorder — ghost clone, scale+rotate+glow on drag, grid-snap drop, PUT persisted `@engineer-1`
- [x] Loading shimmer skeleton while widget JS initializes — 3-bar sweep animation, clears on content inject `@engineer-1`
- [x] 4 starter widget templates — `backend/mcp/widget_templates.py`: stat-counter, sparkline-chart, log-stream, progress-ring `@designer-1`
- [x] Update `.claude/INSTRUCTIONS.md` with canvas MCP tools reference `@engineer-2`
- [ ] `gpush` → Jenkins build + deploy → smoke test — **pending branch merge** `@engineer-2`
- [ ] Cut v1.7.0 release — **pending branch merge** `@engineer-1`
