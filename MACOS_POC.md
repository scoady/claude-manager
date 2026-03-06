# macOS Native App POC -- Claude Manager

## Decision Record

**Framework**: SwiftUI (native macOS, min deployment: macOS 14 Sonoma)
**Scope**: Pure native rewrite -- no embedded Python backend
**Goal**: A first-class macOS app that manages Claude agents directly, using the filesystem and CLI as its data layer

## Architecture Decision: Rewrite, Don't Embed

### Why not embed the Python backend?

The initial instinct was to bundle the FastAPI backend as a managed subprocess. This avoids rewriting 67 endpoints and 16 service modules. But it's the wrong tradeoff:

1. **The HTTP layer is waste.** The Python backend exists to serve a browser over HTTP/WebSocket. A native app doesn't need a web server between its UI and its logic. SwiftUI's `@Observable` + `AsyncStream` from process stdout gives real-time reactivity without any networking.

2. **Bundling Python is fragile.** 80-200MB of venv inside a .app, 3 managed child processes (backend + 2 MCP servers), subprocess crash recovery, port conflicts, PATH issues. This is a Rube Goldberg machine for what's ultimately "read some JSON files and spawn some processes."

3. **The business logic is simple.** Strip away FastAPI routing, Pydantic serialization, WebSocket broadcasting, and CORS middleware. What remains?
   - Scan a directory for projects (read `PROJECT.md`, `manager.json`, `workflow.json`)
   - Spawn `claude --print --output-format stream-json` and parse JSON lines from stdout
   - Read/write JSON files (tasks, workflows, settings, roles, widget templates)
   - Run `git status --porcelain` and list files
   - That's it. All of this is trivial in Swift.

4. **SwiftUI replaces the entire frontend + WebSocket layer.** No DOM, no manual event routing, no WS reconnection logic. Process stdout -> AsyncStream -> @Observable -> SwiftUI view update. The reactive pipeline is built into the platform.

5. **One language, one process, one mental model.** No Python-Swift boundary. No "is the backend healthy?" state machine. No port allocation. The app IS the backend.

6. **The web app doesn't go away.** The Python backend + SPA continues serving `claude-manager.localhost` for remote/browser access. The native app is a parallel client that talks directly to the filesystem and CLI.

### What about the 67 endpoints?

Most endpoints fall into a few patterns:

| Pattern | Count | Swift equivalent |
|---------|-------|-----------------|
| JSON file CRUD (tasks, workflows, roles, settings, templates) | ~30 | `Codable` + `FileManager` read/write |
| Directory scanning (projects, files, skills) | ~10 | `FileManager.contentsOfDirectory` |
| Process spawning (dispatch, inject, plan, orchestrate) | ~10 | `Process` + `AsyncStream` |
| Git operations (status, diff) | ~5 | `Process("git", ...)` |
| Canvas/widget state | ~10 | In-memory state (no persistence needed for native) |
| Health/stats | ~2 | Computed from in-memory state |

The actual unique logic to rewrite is ~1500 lines across 5-6 service modules. The rest is HTTP plumbing that disappears.

## Service Layer Design

```
Services/
|-- ProjectService.swift      # Scan ~/git/claude-managed-projects/, read PROJECT.md + configs
|-- AgentService.swift        # Spawn claude CLI, parse stream-json, manage lifecycle
|-- TaskService.swift         # Read/write TASKS.md, task state machine
|-- WorkflowService.swift     # Read/write workflow.json, phase transitions
|-- FileService.swift         # File listing, content preview, git status
|-- TemplateService.swift     # Workflow templates from bundled JSON + custom
|-- WidgetCatalogService.swift# Widget template CRUD (JSON file store)
|-- SkillService.swift        # Skill discovery, per-project symlink toggle
|-- RoleService.swift         # Role CRUD (~/.claude/roles.json)
|-- SettingsService.swift     # Global settings + plugins read/write
|-- KeychainService.swift     # OAuth token extraction (Security.framework)
```

### AgentService -- the core

This is the only complex service. It replaces `AgentBroker` + `AgentSession` + `WSManager`:

```swift
@Observable
class AgentService {
    var sessions: [AgentSession] = []

    func dispatch(project: Project, task: String, model: String? = nil) -> AgentSession {
        let session = AgentSession(project: project, task: task, model: model)
        sessions.append(session)
        session.start()  // Spawns Process, begins streaming
        return session
    }
}

@Observable
class AgentSession: Identifiable {
    let id = UUID()
    var cliSessionId: String?
    var phase: SessionPhase = .starting
    var streamText: String = ""
    var toolCalls: [ToolCall] = []
    var turnCount: Int = 0
    var statusMarkdown: String = ""

    private var process: Process?

    func start() {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: claudePath)
        proc.arguments = ["--print", "--output-format", "stream-json", task]
        proc.currentDirectoryURL = project.url

        var env = ProcessInfo.processInfo.environment
        env["CLAUDE_CODE_OAUTH_TOKEN"] = KeychainService.getOAuthToken()
        proc.environment = env

        let pipe = Pipe()
        proc.standardOutput = pipe

        // Parse stream-json lines -> update @Observable properties
        Task {
            for try await line in pipe.fileHandleForReading.bytes.lines {
                guard let data = line.data(using: .utf8),
                      let event = try? JSONDecoder().decode(StreamEvent.self, from: data)
                else { continue }
                await MainActor.run { handleEvent(event) }
            }
            await MainActor.run { phase = .idle }
        }

        try? proc.run()
        process = proc
    }

    private func handleEvent(_ event: StreamEvent) {
        switch event.type {
        case "system": cliSessionId = event.sessionId
        case "assistant":
            if let text = event.contentDelta { streamText += text; phase = .generating }
            if let tool = event.toolUse { toolCalls.append(tool); phase = .toolExec }
        case "result": statusMarkdown = event.resultText ?? ""; turnCount += 1; phase = .idle
        default: break
        }
    }

    func inject(message: String) {
        guard let sessionId = cliSessionId else { return }
        // Spawn new process with --resume
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: claudePath)
        proc.arguments = ["--print", "--output-format", "stream-json",
                         "--resume", sessionId, "--message", message]
        // ... same stdout parsing loop
    }
}
```

Because `AgentSession` is `@Observable`, SwiftUI views automatically update when `streamText`, `phase`, `toolCalls`, etc. change. No WebSocket. No event bus. No manual DOM updates.

### ProjectService -- filesystem scanning

```swift
@Observable
class ProjectService {
    var projects: [Project] = []
    private let baseDir: URL  // ~/git/claude-managed-projects/

    func scan() async {
        let entries = try? FileManager.default.contentsOfDirectory(
            at: baseDir, includingPropertiesForKeys: [.isDirectoryKey])
        projects = (entries ?? []).compactMap { url in
            guard url.hasDirectoryPath else { return nil }
            return Project(
                name: url.lastPathComponent,
                url: url,
                projectMd: readOptional(url.appendingPathComponent("PROJECT.md")),
                config: decodeOptional(url.appendingPathComponent(".claude/manager.json"))
            )
        }
    }
}
```

### TaskService, WorkflowService, etc. -- JSON file I/O

All follow the same pattern:

```swift
struct TaskService {
    static func load(for project: Project) -> [TaskItem] {
        let url = project.url.appendingPathComponent("TASKS.md")
        guard let content = try? String(contentsOf: url) else { return [] }
        return parseTasks(content)  // Parse markdown task list
    }

    static func save(_ tasks: [TaskItem], for project: Project) throws {
        let md = tasks.map { "- [\($0.done ? "x" : " ")] \($0.title)" }.joined(separator: "\n")
        try md.write(to: project.url.appendingPathComponent("TASKS.md"), atomically: true, encoding: .utf8)
    }
}
```

## Project Structure

```
claude-manager-macos/
|-- ClaudeManager/
|   |-- App/
|   |   |-- ClaudeManagerApp.swift         # @main entry, WindowGroup, MenuBarExtra
|   |   +-- AppState.swift                 # @Observable: services, navigation state
|   |
|   |-- Models/
|   |   |-- Project.swift                  # Project, ProjectConfig
|   |   |-- Agent.swift                    # AgentSession, SessionPhase, StreamEvent
|   |   |-- Task.swift                     # TaskItem, TaskStatus
|   |   |-- Workflow.swift                 # WorkflowConfig, WorkflowPhase
|   |   |-- Widget.swift                   # WidgetTemplate
|   |   +-- Role.swift                     # RolePreset
|   |
|   |-- Services/
|   |   |-- ProjectService.swift           # Scan projects dir, read configs
|   |   |-- AgentService.swift             # Spawn CLI, parse stream-json, manage sessions
|   |   |-- TaskService.swift              # TASKS.md read/write
|   |   |-- WorkflowService.swift          # workflow.json lifecycle
|   |   |-- FileService.swift              # File listing, preview, git status
|   |   |-- TemplateService.swift          # Workflow template loading
|   |   |-- WidgetCatalogService.swift     # Widget template CRUD
|   |   |-- SkillService.swift             # Skill discovery + symlink toggle
|   |   |-- RoleService.swift              # roles.json CRUD
|   |   |-- SettingsService.swift          # settings.json + plugins
|   |   +-- KeychainService.swift          # OAuth token (Security.framework)
|   |
|   |-- Views/
|   |   |-- ContentView.swift              # NavigationSplitView root
|   |   |-- Sidebar/
|   |   |   |-- SidebarView.swift          # Project list
|   |   |   +-- ProjectRow.swift           # Name + status dot + agent count
|   |   |
|   |   |-- Feed/
|   |   |   |-- FeedView.swift             # Project detail: header + tabs
|   |   |   |-- AgentCard.swift            # Status card with markdown
|   |   |   |-- AgentDetailView.swift      # Full stream + tools (inspector panel)
|   |   |   |-- DispatchBar.swift          # Task input + model picker
|   |   |   +-- ToolBlockView.swift        # Tool call display
|   |   |
|   |   |-- Tasks/
|   |   |   |-- TasksView.swift            # Task list
|   |   |   +-- TaskRow.swift              # Task with status + actions
|   |   |
|   |   |-- Workflow/
|   |   |   |-- WorkflowView.swift         # Phase timeline
|   |   |   +-- PhaseCard.swift            # Phase detail
|   |   |
|   |   |-- Studio/
|   |   |   |-- StudioView.swift           # Widget template grid
|   |   |   |-- TemplateCard.swift         # Card with WKWebView preview
|   |   |   +-- TemplateBuilder.swift      # Generate + preview + save
|   |   |
|   |   +-- Settings/
|   |       |-- SettingsView.swift          # Settings window (Cmd+,)
|   |       |-- SkillsTab.swift
|   |       +-- RolesTab.swift
|   |
|   |-- Components/
|   |   |-- GlassCard.swift                # Frosted glass ViewModifier
|   |   |-- PhaseBadge.swift               # Phase indicator
|   |   |-- StatusDot.swift                # Pulsing dot
|   |   +-- MarkdownView.swift             # AttributedString markdown
|   |
|   +-- Resources/
|       |-- Assets.xcassets
|       +-- templates/                     # Bundled workflow template JSONs
|
|-- ClaudeManager.xcodeproj
+-- Makefile
```

## Phased Implementation

### Phase 1: Core Data Layer + Minimal UI (1 week)

**Goal**: App launches, scans projects directory, shows sidebar, can dispatch an agent and see streaming output.

This validates the two hardest pieces: filesystem scanning and CLI process management.

#### Deliverables
- [ ] Xcode project, SwiftUI lifecycle
- [ ] `AppState` (@Observable): projects, agents, selectedProject
- [ ] `ProjectService` -- scan `~/git/claude-managed-projects/`, load `PROJECT.md` + `manager.json`
- [ ] `AgentService` + `AgentSession` -- spawn `claude --print --output-format stream-json`, parse events
- [ ] `KeychainService` -- extract OAuth token
- [ ] `SidebarView` + `ProjectRow`
- [ ] `FeedView` + `AgentCard` (basic: phase badge + streaming text)
- [ ] `DispatchBar` -- text input + dispatch button

#### Validation criteria
- App scans and lists projects from filesystem
- Dispatch spawns `claude` process, streams output into AgentCard
- Agent phase transitions (starting -> thinking -> generating -> tool_exec -> idle) visible
- Multiple agents can run concurrently

#### Key risk to validate
- Does `Process` spawning work without App Sandbox? (Yes, if sandbox disabled)
- Does `claude` CLI resolve from `/usr/local/bin/claude`?
- Does OAuth token injection via environment variable work?

---

### Phase 2: Tasks + Workflow + Detail Views (1 week)

**Goal**: Full project management -- tasks, workflows, agent detail with tool blocks.

#### Deliverables
- [ ] `TaskService` -- parse/write TASKS.md
- [ ] `TasksView` + `TaskRow` -- list, create, start (dispatch agent), complete
- [ ] `WorkflowService` -- read/write workflow.json, phase state machine
- [ ] `WorkflowView` + `PhaseCard` -- create from template, start, advance/complete
- [ ] `TemplateService` -- load bundled workflow template JSONs
- [ ] `AgentDetailView` -- full stream text + tool call list (in inspector/sheet)
- [ ] `ToolBlockView` -- tool name with SF Symbol, input summary, output summary, duration
- [ ] `MarkdownView` -- render status card markdown via `AttributedString(markdown:)`
- [ ] Inject composer on AgentCard (resume session with follow-up message)

#### Tab structure for FeedView
```
FeedView
|-- Header (project name + dispatch bar)
|-- TabView
    |-- Agents (agent cards -- default)
    |-- Tasks (task list + plan button)
    +-- Workflow (phase timeline + controls)
```

---

### Phase 3: Files + Skills + Roles (1 week)

**Goal**: Complete the supporting features.

#### Deliverables
- [ ] `FileService` -- `FileManager` listing + content preview + `git status --porcelain`
- [ ] File browser view (split: tree + preview with syntax highlighting via `AttributedString`)
- [ ] `SkillService` -- scan `~/.claude/skills/`, per-project symlink toggle
- [ ] Skills panel in feed header (collapsible, toggle switches)
- [ ] `RoleService` -- CRUD on `~/.claude/roles.json`
- [ ] `SettingsService` -- read/write `~/.claude/settings.json`
- [ ] `SettingsView` as native Settings window (Cmd+,)
  - General tab: paths, default model
  - Skills tab: list + toggle
  - Roles tab: list + create/edit/delete

---

### Phase 4: Widget Studio (1 week)

**Goal**: Template catalog browser with live previews.

#### Deliverables
- [ ] `WidgetCatalogService` -- CRUD on widget template JSON files
- [ ] `StudioView` -- LazyVGrid of TemplateCard
- [ ] `TemplateCard` -- WKWebView rendering template HTML/CSS/JS with preview_data
- [ ] `TemplateBuilder` -- prompt input, calls `claude --print` to generate template, preview, save
- [ ] Template `{{placeholder}}` substitution in Swift (regex replace, same logic as JS version)
- [ ] Category badges, delete button, copy-to-clipboard

#### Template generation (no API server needed)
```swift
func generateTemplate(prompt: String) async throws -> WidgetTemplate {
    let systemPrompt = "You are a widget template designer..."  // Same as Python _TEMPLATE_GEN_PROMPT
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: claudePath)
    proc.arguments = ["--print", "--model", "claude-opus-4-6",
                     "--system-prompt", systemPrompt,
                     "--output-format", "text", "--max-turns", "3", prompt]
    // Capture stdout, parse JSON result
    let output = try await runAndCapture(proc)
    return try JSONDecoder().decode(WidgetTemplate.self, from: output.data(using: .utf8)!)
}
```

---

### Phase 5: Native Polish (1 week)

**Goal**: Make it feel like a real macOS app, not a web port.

#### Deliverables
- [ ] `MenuBarExtra` -- agent count, quick actions (new project, pause all)
- [ ] Dock badge -- active agent count
- [ ] Native notifications (`UNUserNotificationCenter`) -- agent complete, agent error
- [ ] Keyboard shortcuts: Cmd+N, Cmd+D, Cmd+K (project search), Cmd+1-5
- [ ] Window state persistence (`@SceneStorage`)
- [ ] Drag & drop .md files onto sidebar to set as PROJECT.md
- [ ] Vaporwave color theme via `Color` extensions + custom `ViewModifier`s
- [ ] Smooth animations: phase transitions, agent spawn/complete, card reveals

#### Color theme
```swift
extension ShapeStyle where Self == Color {
    static var cmBackground: Color { Color(hex: "#0d0221") }
    static var cmSurface: Color { Color(hex: "#1a0a3e") }
    static var cmCyan: Color { Color(hex: "#00f0ff") }
    static var cmMagenta: Color { Color(hex: "#e040fb") }
    static var cmNeonGreen: Color { Color(hex: "#39ff14") }
    static var cmText: Color { Color(hex: "#f0e6ff") }
    static var cmMuted: Color { Color(hex: "#7c4dff") }
}
```

---

## What the Native App Does NOT Need

| Web concern | Why it disappears |
|-------------|-------------------|
| FastAPI + uvicorn | No HTTP server needed -- direct function calls |
| WebSocket event bus | `@Observable` + `AsyncStream` replaces it |
| Pydantic models | Swift `Codable` structs |
| CORS middleware | No cross-origin -- same process |
| Docker container | Native process on macOS |
| Nginx proxy | No proxying needed |
| Vite build | No JS bundling |
| marked.js + DOMPurify | `AttributedString(markdown:)` |
| highlight.js | `NSAttributedString` + `NSTextView` with custom syntax theme |
| MCP servers | Agent MCP configs point to `claude-manager.localhost` API -- still works if web backend is running; or we expose a lightweight local HTTP handler for MCP tools only |

## Open Question: MCP Servers

The MCP canvas and orchestrator servers let agents call tools like `canvas_put` and `create_task`. These are HTTP servers that agents connect to via MCP config JSON.

**Options**:
1. **Keep web backend for MCP only** -- agents still use `http://localhost:4040` for MCP tools. Requires docker-compose running alongside.
2. **Embed minimal HTTP handlers in the Swift app** -- a lightweight `NWListener` or embedded Swift HTTP server that handles just the MCP tool endpoints (~10 routes). No Python needed.
3. **Skip MCP in POC** -- agents work without MCP tools. The orchestrator and canvas features are optional.

**Recommendation for POC**: Option 3. MCP is an enhancement, not core. Dispatch + stream + task management work without it. Add MCP support later as Option 2.

## Dependencies

**Zero third-party Swift packages.** Everything uses Apple frameworks:
- **SwiftUI** -- UI and reactive state
- **Foundation** -- Process, FileManager, JSONDecoder, URLSession
- **WebKit** -- WKWebView for widget template previews
- **Security** -- Keychain access for OAuth token
- **UserNotifications** -- Native notifications

## Risk Register

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| App Sandbox blocks Process spawning | High | High | Disable sandbox (direct .app distribution) |
| `claude` CLI not on PATH in app context | High | Medium | Search known paths: /usr/local/bin, ~/.npm-global/bin, which claude |
| OAuth token Keychain item name unknown | Medium | Medium | Read scripts/start.sh to find exact service/account |
| stream-json format changes in CLI updates | Medium | Low | Defensive parsing, log unknown event types |
| TASKS.md format diverges from web parser | Medium | Medium | Define canonical format, share test fixtures |
| Swift markdown rendering limited vs marked.js | Low | Medium | Use WKWebView fallback for complex markdown |

## Repo Decision

**Subdirectory in existing repo**: `claude-manager/macos/`

Rationale:
- Shared workflow template JSONs (`backend/templates/`) can be referenced directly
- TASKS.md and workflow.json formats stay in sync
- Single repo for issues, PRs, releases
- `.gitignore` already handles build artifacts
- The native app is a UI layer over the same project structure

## Timeline

| Phase | Duration | Milestone |
|-------|----------|-----------|
| 1. Core + Minimal UI | 1 week | Dispatch agent, see streaming output |
| 2. Tasks + Workflow | 1 week | Full project management |
| 3. Files + Skills + Roles | 1 week | Supporting features |
| 4. Widget Studio | 1 week | Template catalog with previews |
| 5. Native Polish | 1 week | Menu bar, notifications, theme |
| **Total** | **5 weeks** | **Feature-complete POC** |
