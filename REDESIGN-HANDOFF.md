# Claude Manager — Redesign Handoff Document

> Date: 2026-03-05
> Purpose: Context package for creative agents working on dashboard redesign wireframes

---

## 1. Current Architecture

### Stack
- **Frontend**: Vite SPA (vanilla JS, no framework) → built by Jenkins → served from Nginx pod in kind cluster (`claude-manager.localhost`)
- **Backend**: FastAPI (Python) in docker-compose on host (port 4040), reverse-proxied by Nginx
- **Agents**: `claude --print --output-format stream-json` subprocesses managed by AgentBroker
- **Canvas**: GridStack 12-col grid with widgets rendered via `new Function('root','host', jsCode)`

### Key Files
```
frontend/
  index.html                    — SPA entry, imports all JS modules
  css/
    variables.css               — design tokens (colors, spacing, typography)
    base.css                    — reset, body, scrollbars
    layout.css                  — app shell, sidebar, main area
    feed.css                    — feed/canvas area, tab bar, controls
    sidebar.css                 — project list, agent cards
    settings.css                — settings modal
  js/
    app.js                      — bootstrap, router, WebSocket
    state.js                    — global state store
    ws.js                       — WebSocket client
    sidebar/
      Sidebar.js                — project list + agent cards
    feed/
      FeedController.js         — main content area (overview, tasks, milestones, workflow, canvas, artifacts)
      OverviewPanel.js          — project overview tab
      TasksPanel.js             — tasks tab
    canvas/
      CanvasEngine.js           — GridStack setup, widget lifecycle, layout persistence
      WidgetFrame.js            — widget rendering, context menu, sandboxed execution
      WidgetStudio.js           — widget template browser/creator

backend/
  main.py                       — FastAPI app, all API routes
  models.py                     — Pydantic models
  services/
    agent_broker.py             — agent process management
    canvas.py                   — canvas widget CRUD + persistence
    widget_catalog.py           — template store (~/.claude/canvas/templates/)
    projects.py                 — project discovery
    workflows.py                — workflow orchestration
    roles.py                    — role/persona management
  mcp/
    canvas_server.py            — MCP tools for agents (canvas_put, canvas_design, etc.)
```

### Widget Execution Model
- Widgets are bare JS function bodies receiving `(root, host)` parameters
- Executed via `new Function('root', 'host', jsCode)(containerDiv, hostDiv)`
- Can create any DOM elements (canvas, SVG, divs) inside `root`
- Cross-widget communication via `window.__starNames`, custom events
- Template system: `{{placeholder}}` substitution with `{{#each}}` loops

### Canvas System
- GridStack 12-column, cellHeight=55px, margin=4, float=true
- Widgets have: `gs_x, gs_y, gs_w, gs_h` (grid position/size), `title`, `html`, `css`, `js`, `tab`
- Tab system: frosted glass pill bar, filter-by-tab, add/rename/delete tabs
- Persistence: JSON files in `~/.claude/canvas/<project>/`
- `no_resize`, `no_move` flags for locking widgets in place

---

## 2. Design Language & User Preferences

### Void Aesthetic (current)
- Dark backgrounds: `#030509`, `#0a0f15`
- Accent colors: cyan `#67e8f9`, amber `#fbbf24`, purple `#a78bfa`, green `#34d399`, rose `#f43f5e`
- Glow effects, backdrop-filter blur, frosted glass panels
- Constellation/star map is the hero visual — agents named after real stars

### User's Confirmed Preferences
- Particle effects, constellation animations, glowing neon accents
- Clean snappy UI that is also dynamic — no blocky transitions
- No fixed pixel sizes — fluid, auto-smoothing layout
- CSS transitions/animations everywhere, smooth interpolation, organic feel
- Shooting stars, ripple rings, pulsing glows, staggered reveals
- Frosted glass overlays, radial gradients for depth
- Space/astronomy theme
- Wants **interactive/functional widgets** (not just eye candy) — 30% interactive, 30% data-driven, 20% creative art, 20% experimental

---

## 3. Frontend Library Starter Kit (from research)

All libraries support CDN lazy-loading. Widgets declare deps, CanvasEngine loads on demand.

| Priority | Library | Size (gzip) | Best For |
|----------|---------|-------------|----------|
| 1 | **Three.js** | 180 KB | 3D scenes, particle galaxies, WebGL shaders |
| 2 | **GSAP** | 25 KB | Buttery animation, timeline sequencing |
| 3 | **D3.js** (modular) | 30-90 KB | Data viz, force graphs, treemaps |
| 4 | **xterm.js** | 90 KB | Real terminal emulator in widgets |
| 5 | **p5.js** (instance) | 100 KB | Generative art, creative coding |
| 6 | **Cytoscape.js** | 112 KB | Agent/task dependency graphs |
| 7 | **Anime.js v4** | 10 KB | Lightweight DOM animation |

### Additional High-Value Libraries
- **PixiJS v8** (200KB) — 2D WebGL sprites, filters, particle containers
- **Matter.js** (30KB) — Physics simulation for interactive widgets
- **Lottie** (82KB) — After Effects animations as JSON
- **GlslCanvas** (15KB) — Raw GLSL shaders in widgets
- **Konva.js** (55KB) — Interactive canvas with drag/drop, hit detection
- **HLS.js** (70KB) — Live video streaming from agents
- **CodeMirror 6** (124KB) — Code editor widget

### CDN Loading Strategy
```javascript
// Widget declares dependencies
// CanvasEngine loads before execution:
await loadCDN('three', 'https://cdn.jsdelivr.net/npm/three@0.172/build/three.min.js');
// Then widget JS has access to THREE global
```

### WebGL Context Management
- Browsers cap at 8-16 active WebGL contexts
- For multiple 3D widgets: share a single offscreen canvas with scissor/viewport technique
- Or limit to 2-3 simultaneous Three.js widgets

---

## 4. Remotion Research Highlights

11 Remotion projects explored — key patterns for the redesign:

- **Audio-reactive visualizations**: Tool calls as frequency bars with attack/decay envelopes
- **Constellation flythrough**: 7-scene cinematic camera with keyframe interpolation, 300 background stars, nebula clouds, burst particles
- **DAW interface**: Web Audio synthesis + Remotion Player in a split-pane editor
- **Session replay**: Agent events animated on a timeline with tool-type color coding
- **Dashboard recorder**: Frame capture → video export with constellation fallback

### Shared Patterns
- All use void aesthetic (#030509 bg, neon accents)
- Canvas 2D and SVG rendering dominate (not WebGL)
- Easing functions for smooth camera/transition interpolation
- Scanline overlays and vignettes as post-processing
- Particle systems with seeded random generation for determinism

---

## 5. Widget Generator Learnings

### What Worked
- `canvas_design` MCP tool with rich intent descriptions produces the best widgets
- Varied col_span/row_span (3x3 to 12x5) creates visual interest
- Mixing generative art, data viz, retro, nature, and abstract categories
- Tab system for organizing widgets into separate views

### What Didn't Work / Gotchas
- **IIFE wrapping**: Design agent (Haiku) wraps JS in `(function(root,host){...})` which shadows params — backend now strips IIFEs
- **clientWidth=0**: Widgets created before container is visible get zero dimensions — need ResizeObserver or delayed init
- **Template ID mismatch**: Filenames don't always match internal `id` field — need fallback scan
- **Agent fragility**: Widget generator agents die frequently — need robust error recovery

---

## 6. Redesign Opportunities

### A. App Shell Redesign
- Current: sidebar (project list + agents) + main content area (tabs)
- Opportunity: command palette (Cmd+K), floating panels, collapsible sidebar with constellation mini-map
- Consider: full-screen canvas mode with overlay controls, dock-style widget palette

### B. Canvas Evolution
- Lazy CDN loading for library-powered widgets
- Shared WebGL context manager for Three.js widgets
- Widget dependency declarations (what libs they need)
- Widget-to-widget data pipes (not just window globals)
- Canvas recording/export to video (leveraging Remotion research)

### C. Agent Visualization
- 3D constellation map (Three.js) replacing current 2D canvas
- Agent session replay as animated timeline widgets
- Real-time agent activity heatmap
- Audio-reactive agent activity visualization

### D. Interactive Widgets
- Clickable task kanban with drag-and-drop status changes
- Code editor widgets (CodeMirror) for quick edits
- Terminal widget powered by xterm.js (real shell sessions)
- Graph explorer for agent/task dependencies (Cytoscape.js)
- File browser with syntax-highlighted preview

### E. Data Dashboard
- Real-time metrics: agent throughput, token usage, task completion rates
- Sparkline charts, progress rings, heat calendars
- Sankey diagrams showing agent → task → artifact flows

---

## 7. Constraints & Ground Rules

- **No node/npm on host**: Frontend builds go through Jenkins CI/CD
- **Backend startup**: MUST use `scripts/start.sh` (extracts OAuth from macOS Keychain)
- **Deploy**: `gpush` → Jenkins build → deploy → k8s
- **Widget JS**: Bare function bodies, NO IIFEs, receives `(root, host)`
- **GridStack**: 12-col, cellHeight=55px — design within this grid
- **Browser target**: Modern Chrome/Safari (WebGL2, CSS backdrop-filter, ES2022+)
- **Keep existing API contracts**: `/api/canvas/`, `/api/projects/`, `/api/agents/`, WebSocket events

---

## 8. For Creative Agents

### Frontend Design Agent
Focus on: visual language, component design, interaction patterns, CSS architecture, animation system. Create wireframe widgets that demonstrate the new design language. Use `canvas_design` MCP tool to place wireframes directly on the canvas.

### Architect Agent
Focus on: app shell layout, navigation flow, state management, CDN loader implementation, WebGL context sharing, widget dependency system, canvas recording pipeline. Write technical specs and place architecture diagrams on the canvas.

### Collaboration
- Both agents work on the same canvas but different tabs
- Frontend design uses tab "wireframes"
- Architect uses tab "architecture"
- Cross-reference each other's work via `canvas_list`

---

*Generated from: FRONTEND-LIBS-RESEARCH.md (1201 lines), remotion-report.md, widget generator session output, project memory*
