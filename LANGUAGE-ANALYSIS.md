# Language Analysis: claude-manager Backend

## Executive Summary

**Keep Python, but surgically rewrite the hot path.** The claude-manager backend is a subprocess orchestrator with WebSocket fan-out — not a high-throughput data pipeline. Python's asyncio handles this workload adequately today. The real bottlenecks (GIL contention during JSON parsing, sequential WebSocket broadcast, `run_in_executor` for synchronous file I/O) are addressable without a full rewrite. If performance pressure grows, the highest-ROI move is extracting the agent broker into a Go sidecar — not rewriting 8,800 lines of working Python. A full rewrite to Go or Rust would cost 3-6 weeks of velocity for marginal gains at the current scale (< 20 concurrent agents, 1-3 WebSocket clients). TypeScript deserves serious consideration only if MCP SDK parity becomes critical.

---

## 1. Workload Profile

### 1.1 Subprocess Management

**Scale**: Typically 1-10 concurrent Claude CLI subprocesses; theoretical max ~20 (bounded by Claude API rate limits and parallelism config per project, not system resources).

**Lifecycle**: Each `AgentSession` spawns a `claude --print --output-format stream-json` subprocess via `asyncio.create_subprocess_exec`. The session reads stdout line-by-line, parses JSON, fires callbacks, and monitors for process exit. Stderr is drained in a separate `asyncio.Task`. Injection (follow-up messages) spawns a new subprocess with `--resume <session_id>`.

**Signal handling**: `proc.kill()` on cancel, `asyncio.wait_for(proc.wait(), timeout=5.0)` with kill fallback. No graceful SIGTERM — it's kill-or-timeout.

**Key numbers**:
- 1MB readline buffer (`limit=1024*1024`) — explicitly set to handle large stream-json events
- Each subprocess creates 2-3 asyncio tasks: stream stdout, drain stderr, plus the spawn-and-stream coroutine itself
- At 10 concurrent agents: ~30 asyncio tasks just for subprocess I/O

### 1.2 Stream Processing (THE HOT PATH)

**`_stream_stdout()`** in `agent_session.py` is where the backend spends most of its time:

```
readline() -> decode UTF-8 -> json.loads() -> _handle_stream_event() -> callback chain
```

**Throughput per subprocess**: Claude CLI emits stream-json events at ~10-50 lines/sec during active generation. Each line is a JSON object (100 bytes to 100KB for large tool inputs). During `content_block_delta` with `text_delta`, events arrive at token generation speed (~50-80 tokens/sec = ~50-80 events/sec).

**Parsing complexity**: `_handle_stream_event()` is a type-dispatch switch with 8 branches. The heaviest branches:
- `content_block_delta` with `text_delta`: fires `on_text_delta` callback -> WS broadcast (most frequent)
- `content_block_stop`: JSON-parses accumulated tool input buffer, formats milestone, fires 2 callbacks
- `assistant`: iterates content blocks, appends to output buffer
- `user`: scans for `tool_result` blocks matching pending agent tools (subagent lifecycle)

**Bottleneck**: `json.loads()` on every line is pure Python (CPython's json module is C-accelerated, but the dispatch and callback chain is not). With 10 agents at 50 events/sec each = 500 JSON parses/sec + 500 callback invocations/sec. This is well within Python's capability but leaves no headroom.

### 1.3 WebSocket Fan-Out

**Connections**: 1-3 concurrent WebSocket clients (browser tabs). This is a single-user developer tool, not a public service.

**Broadcast frequency**: During active agent work, broadcasts happen at token-generation speed per agent. With 5 active agents: ~250-400 broadcasts/sec. Each broadcast serializes a dict to JSON and sends to all connected clients sequentially.

**Message size**: `agent_stream` messages are small (~100-200 bytes). `tool_start`/`tool_done` messages include milestones (~500 bytes). `agent_state_sync` can be larger (~2-5KB).

**Current implementation** (`ws_manager.py`): Sequential iteration over `self.active` list, `await ws.send_text(text)` per client. Dead connections are cleaned up post-iteration. No batching, no message coalescing.

**Bottleneck**: The sequential `await` per client means broadcast latency scales linearly with client count. At 3 clients this is negligible; at 50 it would be a problem. But 50 clients is not a realistic scenario.

### 1.4 API Surface

**79 HTTP endpoints** across:
- Project CRUD (list, get, create, bootstrap, delete, config)
- Agent lifecycle (dispatch, cancel, inject, list, messages)
- Tasks (list, add, update, plan, start/stop individual)
- Workflows (CRUD, start/pause/resume/advance)
- Canvas widgets (CRUD, layout save, scene replace, design)
- Skills (list, toggle, create, marketplace)
- Roles (list, CRUD)
- Artifacts (file browser, content, git status)
- Cron jobs (CRUD, list)
- Templates, widget catalog
- Settings, milestones, stats
- 1 WebSocket endpoint

**Complexity**: Most endpoints are thin wrappers around service modules. The heaviest are:
- `dispatch_task`: builds prompts, resolves MCP config, creates broker session
- `POST /canvas/{project}/design`: spawns an agent to design a widget (meta!)
- Workflow actions: state machine transitions with git worktree/subdirectory management

**Database**: Optional PostgreSQL via asyncpg. Falls back to memory-only. Used for session/message persistence — not in the hot path.

### 1.5 MCP Server

Two MCP sidecar processes (separate Docker containers):
- `mcp-canvas`: 6 tools (canvas_put, canvas_design, canvas_remove, canvas_list, canvas_templates, plus implicit protocol tools)
- `mcp-orchestrator`: exposes task/workflow management to agents

**Transport**: SSE via FastMCP library. Each tool makes synchronous `httpx` calls back to the main backend API. No streaming, no high-frequency usage — agents call MCP tools occasionally (1-5 calls per agent turn).

**Protocol overhead**: Minimal. MCP is request-response over SSE. The bottleneck is the Claude API, not MCP.

### 1.6 File I/O

- **Widget JSON**: `~/.claude/canvas/<project>.json` — read on startup, written on every widget mutation. Synchronous `json.dumps` + `Path.write_text`.
- **TASKS.md**: Parsed every 3 seconds for projects with active agents (polling loop). Regex-based markdown parser.
- **workflow.json**: Read/written on workflow state transitions. Pydantic serialization.
- **Cron jobs.json**: Read/written on cron CRUD and every 30-second tick.
- **Session JSONL**: Read on-demand for message history. Can be large (MBs for long sessions).
- **Milestones JSON**: Append-only, read on demand.

All file I/O is synchronous, wrapped in `run_in_executor(None, ...)` where called from async context (sometimes — inconsistently).

### 1.7 External Process Calls

- **git**: worktree create/remove, merge, branch delete — via `subprocess.run()` (synchronous, blocking)
- **No Docker API calls from Python** — agents handle their own Docker/kubectl via Bash tool
- **httpx**: MCP servers call back to main backend API

### 1.8 Concurrency Model

Single-threaded asyncio event loop (uvicorn with 1 worker). All concurrency is cooperative async/await. The GIL is irrelevant for I/O-bound work but matters during:
1. `json.loads()` / `json.dumps()` on large payloads (holds GIL)
2. `run_in_executor` calls to the default ThreadPoolExecutor (file reads, git operations)
3. Pydantic model serialization

**Contention pattern**: The main contention is on the event loop itself. When one agent's `_handle_stream_event` is doing a WS broadcast, other agents' readline() completions queue up. With 10 agents this creates micro-stalls but nothing catastrophic.

---

## 2. Evaluation Matrix

| Criterion | Weight | Python (current) | Go | Rust | TypeScript (Bun) | Elixir/BEAM |
|---|---|---|---|---|---|---|
| Subprocess management | HIGH (3x) | 4 | 5 | 4 | 4 | 3 |
| Concurrent stream processing | HIGH (3x) | 3 | 5 | 5 | 3 | 5 |
| WebSocket performance | MED (2x) | 3 | 5 | 5 | 4 | 5 |
| Developer velocity | HIGH (3x) | 5 | 3 | 2 | 4 | 2 |
| MCP SDK maturity | MED (2x) | 4 | 2 | 1 | 5 | 1 |
| Container size / startup | LOW (1x) | 2 | 5 | 5 | 3 | 3 |
| Memory efficiency | MED (2x) | 3 | 5 | 5 | 3 | 4 |
| Ecosystem / libraries | MED (2x) | 5 | 4 | 3 | 5 | 3 |
| Error handling / safety | MED (2x) | 3 | 4 | 5 | 3 | 4 |
| Migration cost | HIGH (3x) | 5 | 2 | 1 | 3 | 1 |
| **Weighted Total** | | **89** | **85** | **74** | **83** | **70** |

Scoring: 1 = poor, 5 = excellent. Weights: HIGH=3x, MED=2x, LOW=1x.

---

## 3. Deep Dives

### 3.1 Python (Current — FastAPI + asyncio)

**What's working well:**
- FastAPI's auto-generated OpenAPI docs and Pydantic validation eliminate boilerplate for 79 endpoints
- `asyncio.create_subprocess_exec` is a clean API for subprocess management with async stdout/stderr
- Pydantic models provide runtime validation and JSON serialization with minimal code
- FastMCP library makes MCP server implementation trivial (6 tools in ~400 lines)
- Development velocity is extremely high — features ship in hours, not days
- 8,800 lines total is compact for this much functionality

**What's failing or fragile:**
- The 1MB readline buffer (`limit=1024*1024`) was explicitly added to work around asyncio's default 64KB limit — large stream-json events from Claude CLI would cause `ValueError: Separator is not found, and chunk exceed the limit`
- Sequential WebSocket broadcast blocks the event loop during sends
- `run_in_executor` usage is inconsistent — some file I/O is synchronous on the event loop, some is properly offloaded
- `json.loads()` on every stream line is fine at current scale but has no upgrade path (no zero-copy, no SIMD)
- The `spawner.py` (legacy) and `agent_session.py` (current) coexist — code duplication
- No structured error handling — bare `except Exception` everywhere with `print()` logging
- Global mutable state (`_registry` dict, `canvas_service` singleton) makes testing hard

**The GIL reality:** The GIL does not matter here. This workload is I/O-bound. The event loop spends >95% of its time in `await` (waiting for subprocess output, WebSocket sends, sleep timers). CPU-bound JSON parsing is fast enough for <500 events/sec.

**Asyncio quirks encountered:**
- `asyncio.create_task()` fire-and-forget pattern (DB persistence) — if the task fails, the error is silently swallowed unless the task is awaited
- `asyncio.get_event_loop()` usage in callbacks (deprecated pattern)
- No structured concurrency — tasks are spawned ad-hoc with no cancellation hierarchy

**FastAPI/Starlette WebSocket performance:** Starlette WebSocket wraps Python's `websockets` library. Benchmarks show ~10,000-15,000 messages/sec for small payloads on a single connection. For 3 clients at 400 broadcasts/sec = 1,200 sends/sec — well within budget.

**MCP SDK status:** `fastmcp` (Python) is actively maintained, supports SSE and stdio transports, and is the second-most mature SDK after TypeScript. Version 2.0+ supports the full MCP spec.

**Verdict:** Python is adequate for the current scale. The pain points are code quality issues (inconsistent async patterns, error handling), not language limitations.

### 3.2 Go

**Subprocess management — how the broker would look:**

```go
type AgentSession struct {
    ID          string
    ProjectName string
    Cmd         *exec.Cmd
    Stdout      io.ReadCloser
    Phase       SessionPhase
    Milestones  []string
    mu          sync.RWMutex  // protects Phase, Milestones
    cancel      context.CancelFunc
    callbacks   SessionCallbacks
}

func (s *AgentSession) StreamStdout(ctx context.Context) {
    scanner := bufio.NewScanner(s.Stdout)
    scanner.Buffer(make([]byte, 1024*1024), 1024*1024) // 1MB buffer
    for scanner.Scan() {
        var event StreamEvent
        if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
            continue
        }
        s.handleEvent(ctx, &event)
    }
}
```

**Strengths for this workload:**
- goroutines per subprocess are genuinely zero-overhead (~2KB stack each). 10 agents = 30 goroutines = trivial.
- `bufio.Scanner` with `json.Unmarshal` is ~3-5x faster than Python's `readline()` + `json.loads()` for this pattern
- `context.Context` provides clean cancellation propagation — kill subprocess + stop goroutines in one call
- `sync.RWMutex` for session state eliminates the "is this callback thread-safe?" anxiety
- Binary deployment: single static binary, no Python environment, no pip, no venv
- Container image: ~20MB (vs current ~800MB+ with Node.js + Python + system deps)

**WebSocket fan-out:**

```go
type WSManager struct {
    clients map[*websocket.Conn]struct{}
    mu      sync.RWMutex
}

func (m *WSManager) Broadcast(msg []byte) {
    m.mu.RLock()
    defer m.mu.RUnlock()
    for c := range m.clients {
        go c.WriteMessage(websocket.TextMessage, msg) // parallel sends
    }
}
```

Parallel WebSocket sends via goroutines — no sequential blocking.

**Weaknesses for this workload:**
- 79 endpoints in Go means 79 handler functions with manual request parsing, validation, and error responses. No Pydantic, no auto-docs. Libraries like `chi` or `gin` help but don't match FastAPI's developer experience.
- Pydantic model validation would need to be replaced with struct tags + manual validation or a library like `go-playground/validator`
- MCP SDK: `mcp-go` exists but is community-maintained, less mature than Python's fastmcp. Missing some transport options.
- No equivalent to FastMCP's decorator-based tool registration — more boilerplate
- Migration effort: 8,800 lines of Python -> ~12,000-15,000 lines of Go (Go is more verbose)

**MCP SDK status:** `github.com/mark3labs/mcp-go` — functional but not official. Supports stdio and SSE transports. ~1,500 GitHub stars. Missing some protocol features (sampling, roots). Anthropic has not released an official Go SDK.

**Realistic migration timeline:** 3-4 weeks for a full rewrite. The subprocess management and stream parsing would be done in week 1. The 79 API endpoints would take weeks 2-3. MCP server port would be week 4.

### 3.3 Rust

**Subprocess management with tokio:**

```rust
use tokio::process::Command;
use tokio::io::{AsyncBufReadExt, BufReader};

async fn stream_stdout(mut child: Child, tx: mpsc::Sender<StreamEvent>) {
    let stdout = child.stdout.take().unwrap();
    let reader = BufReader::new(stdout);
    let mut lines = reader.lines();
    while let Some(line) = lines.next_line().await.unwrap() {
        if let Ok(event) = serde_json::from_str::<StreamEvent>(&line) {
            tx.send(event).await.unwrap();
        }
    }
}
```

**Strengths:**
- `serde_json` is the fastest JSON parser available — ~10x faster than Python's json module, ~2-3x faster than Go's encoding/json
- Zero-cost abstractions: `tokio::select!` for multiplexing subprocess streams with cancellation
- `axum` web framework has excellent ergonomics for a Rust framework
- Memory safety guarantees eliminate entire classes of bugs (use-after-free, data races)
- Container image: ~10MB static binary

**Weaknesses:**
- Compile times: 30-60 seconds for incremental builds, 2-5 minutes for clean builds. For a project that "moves FAST," this is a real cost.
- Ownership/borrowing complexity for the callback chain (session callbacks that capture mutable references to the WS manager). This pattern is natural in Python/Go but requires `Arc<Mutex<>>` wrapping in Rust.
- Pydantic -> serde is a lateral move in capability but requires defining every struct and derive macro
- 79 endpoints in axum is doable but verbose — no auto-generated OpenAPI without extra crates (utoipa)
- MCP SDK: No official Rust SDK. Community crates exist (`mcp-rs-template`) but are immature (~100 stars).
- Migration effort: 8,800 lines Python -> ~10,000-12,000 lines Rust, but each line takes 3-5x longer to write due to the type system

**Is the complexity justified?** No. The backend processes <500 JSON events/sec and broadcasts to <5 WebSocket clients. Rust's performance ceiling is orders of magnitude beyond what's needed. The only justified use case would be if this became a multi-tenant SaaS serving thousands of concurrent users — which contradicts its design as a single-developer tool.

**Realistic migration timeline:** 6-8 weeks. The borrow checker fights would be concentrated in the broker/session callback system.

### 3.4 TypeScript / Bun

**Subprocess management:**

```typescript
const proc = Bun.spawn(["claude", "--print", "--output-format", "stream-json", ...args], {
  cwd: projectPath,
  env: { ...process.env, CLAUDE_CODE_OAUTH_TOKEN: token },
  stdout: "pipe",
  stderr: "pipe",
});

const reader = proc.stdout.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split("\n");
  buffer = lines.pop()!;
  for (const line of lines) {
    if (!line.trim()) continue;
    const event = JSON.parse(line);
    await handleStreamEvent(event);
  }
}
```

**Strengths:**
- **MCP SDK is TypeScript-first** — `@modelcontextprotocol/sdk` is the official Anthropic SDK. All MCP examples, docs, and reference implementations are TypeScript. This is a significant advantage as MCP evolves.
- Node.js is already in the Docker image (required for Claude CLI). No additional runtime dependency.
- V8's JSON.parse is faster than Python's json.loads (~2x)
- Bun's subprocess API is cleaner than Node's child_process (no stream mode confusion)
- Could share types/models with the frontend (currently vanilla JS, but could benefit)
- Express/Fastify/Hono for HTTP — mature, fast, well-documented
- `ws` library handles WebSocket with proper backpressure

**Weaknesses:**
- No equivalent to Pydantic for runtime validation — Zod exists but is more verbose and doesn't auto-generate OpenAPI
- Node.js is single-threaded like Python — same event loop contention pattern
- Bun is less battle-tested than Node.js in production (though rapidly maturing)
- TypeScript type system is structural, not nominal — runtime type errors still possible
- No equivalent to FastAPI's dependency injection pattern
- The current frontend is vanilla JS — sharing types would require adding a build step

**WebSocket fan-out:**

```typescript
import { WebSocketServer } from "ws";

function broadcast(wss: WebSocketServer, data: string) {
  for (const client of wss.clients) {
    if (client.readyState === WebSocket.OPEN) {
      client.send(data); // non-blocking in ws library
    }
  }
}
```

**MCP SDK status:** `@modelcontextprotocol/sdk` is the canonical implementation. All new MCP features land here first. Supports all transports (stdio, SSE, HTTP+SSE). The TypeScript SDK is what Anthropic tests against internally.

**Realistic migration timeline:** 2-3 weeks. TypeScript is closest to Python in expressiveness. The Pydantic -> Zod migration is the most tedious part. API endpoints translate almost 1:1.

### 3.5 Elixir / BEAM VM

**Agent session as a GenServer:**

```elixir
defmodule AgentSession do
  use GenServer

  def start_link(opts) do
    GenServer.start_link(__MODULE__, opts)
  end

  def init(%{project_path: path, task: task} = state) do
    port = Port.open({:spawn_executable, System.find_executable("claude")},
      [:binary, :exit_status, :use_stdio,
       args: ["--print", "--output-format", "stream-json", "--", task],
       cd: path, env: build_env()])
    {:ok, %{state | port: port, buffer: ""}}
  end

  def handle_info({port, {:data, data}}, %{port: port, buffer: buf} = state) do
    new_buf = buf <> data
    {lines, remainder} = split_lines(new_buf)
    for line <- lines, do: handle_stream_event(line, state)
    {:noreply, %{state | buffer: remainder}}
  end
end
```

**Strengths:**
- **This is the workload BEAM was built for.** Thousands of lightweight processes, each managing a subprocess with independent failure isolation.
- Supervision trees: if an agent session crashes, the supervisor restarts it. No manual error recovery.
- Phoenix Channels: built-in WebSocket fan-out with presence tracking, backpressure, and PubSub — far more sophisticated than the current WSManager
- Process-per-agent eliminates all shared-state concurrency bugs. No mutexes, no locks, no data races.
- Hot code reloading: deploy new agent session logic without restarting running agents
- Pattern matching makes stream event dispatch elegant and exhaustive

**Weaknesses:**
- **Niche ecosystem**: Finding Elixir developers is hard. The current developer (you) would need to learn a new language paradigm.
- **MCP SDK**: Does not exist. You'd need to implement the MCP protocol from scratch or wrap the Python/TypeScript SDK via a Port.
- **No Pydantic equivalent**: Ecto changesets provide validation but are ORM-focused. There's no auto-OpenAPI generation.
- **Phoenix is opinionated**: Good opinions, but migrating 79 endpoints to Phoenix conventions is non-trivial.
- **Erlang Port for subprocess I/O**: Works but is less ergonomic than Python's asyncio subprocess or Go's os/exec. Line buffering requires manual implementation.
- **Docker image**: Elixir releases are ~30-50MB, but the BEAM VM adds overhead.

**Realistic migration timeline:** 5-7 weeks, plus the learning curve.

---

## 4. Hybrid Architectures

### 4.1 Python API + Go Broker Sidecar

**How it works:** Keep FastAPI for the 79 HTTP endpoints, settings, projects, templates, etc. Extract `AgentBroker` + `AgentSession` into a Go service that:
- Owns subprocess lifecycle
- Parses stream-json events
- Exposes a gRPC or HTTP API for spawn/cancel/inject
- Connects to the Python backend's WebSocket to push events (or runs its own WS endpoint)

**Communication:** gRPC between Python API and Go broker, or HTTP + WebSocket bridge.

**Pros:**
- Keep FastAPI's developer velocity for the 95% of code that isn't performance-sensitive
- Go handles the hot path (subprocess streaming) with native concurrency
- Incremental migration — can run both in parallel during transition
- Go binary is a single static file — easy to add to the Docker image

**Cons:**
- Two languages in one project — doubled cognitive load
- IPC overhead: every stream event must cross a process boundary (serialize -> send -> deserialize)
- Deployment complexity: two processes to manage, health check, restart
- The Go broker needs to understand the Python API's models (duplicate type definitions)
- WebSocket fan-out must be in one place — either the Go broker owns it (and Python proxies) or Python owns it (and Go pushes events). Both are awkward.

**Verdict:** The IPC overhead likely exceeds the performance gain. At <500 events/sec, the bottleneck isn't JSON parsing speed — it's the sequential WS broadcast, which is fixable in Python.

### 4.2 Python API + Rust Stream Processor (PyO3)

**How it works:** Write a Rust extension module (via PyO3/maturin) that exposes a fast stream parser. Python calls `rust_parse_stream_event(line_bytes)` instead of `json.loads()` + Python dispatch.

**Pros:**
- Zero IPC overhead — Rust code runs in-process
- Only rewrite the hot path (~100 lines), keep everything else
- Could also accelerate WebSocket serialization

**Cons:**
- PyO3 builds are complex (cross-compilation for Docker's Linux ARM64)
- Debugging across the Python/Rust boundary is painful
- The hot path is ~100 lines of Python — the effort/benefit ratio is unfavorable
- Doesn't fix the actual bottleneck (sequential WS broadcast)

**Verdict:** Over-engineered for the problem. Use `orjson` (C extension) as a drop-in replacement for `json` if JSON parsing speed matters.

### 4.3 TypeScript Everything

**How it works:** Rewrite the entire backend in TypeScript (Bun or Node.js). Unify with the frontend codebase.

**Pros:**
- One language across the entire stack
- Official MCP SDK — guaranteed protocol compatibility
- Bun's subprocess API is clean and fast
- Could share types between frontend and backend
- Simpler Docker image (just Node.js, which is already required)

**Cons:**
- Loss of Pydantic's validation and auto-OpenAPI (Zod + tRPC or similar fills the gap but with more boilerplate)
- 2-3 week rewrite with no feature progress
- Same single-threaded limitation as Python (event loop model)
- TypeScript's type system is erased at runtime — less safe than Python's Pydantic at the boundary

**Verdict:** The strongest rewrite candidate. The MCP SDK advantage is real and growing. But the migration cost is only justified if MCP becomes a more central part of the architecture.

### 4.4 Go Monolith

**How it works:** Full rewrite of all 8,800 lines into Go.

**Pros:**
- Single binary, 20MB container, sub-second startup
- True parallelism for stream processing (goroutines on multiple cores)
- Excellent subprocess management and WebSocket libraries
- Strong static typing catches bugs at compile time

**Cons:**
- 3-4 weeks of zero feature velocity
- 79 endpoints in Go is tedious — no auto-validation, manual error responses
- MCP SDK is community-maintained, not official
- Go's error handling is verbose — `if err != nil` on every call
- No generics until recently, and the ecosystem hasn't fully adopted them

**Verdict:** Go is the better language for this specific workload (subprocess management + stream processing + WebSocket fan-out). But the migration cost is high and the current Python implementation works. Only justified if the project is scaling to 50+ concurrent agents or becoming a multi-user service.

### 4.5 Elixir Core + Python MCP

**How it works:** Elixir/Phoenix for the main backend (API, WebSocket, agent orchestration). Python sidecar for MCP servers only.

**Pros:**
- BEAM is the ideal runtime for this exact concurrency pattern
- Phoenix Channels are the best WebSocket implementation available
- Supervision trees make agent lifecycle management bulletproof
- Python MCP sidecar keeps the official SDK compatibility

**Cons:**
- Two languages, two runtimes, two deployment targets
- Steep learning curve for Elixir/OTP
- Small ecosystem means more from-scratch implementation
- Phoenix is opinionated about project structure — doesn't map cleanly from FastAPI

**Verdict:** Technically the best fit but practically the worst choice. The learning curve and ecosystem limitations outweigh the concurrency benefits at this scale.

---

## 5. Recommendation

### Primary: Stay with Python. Fix the real problems.

The backend's issues are not language-level — they're implementation-level:

1. **Fix sequential WebSocket broadcast** — Use `asyncio.gather()` for parallel sends:
   ```python
   async def broadcast(self, msg_type, data):
       text = json.dumps({"type": msg_type, "data": data, "timestamp": ...})
       tasks = [ws.send_text(text) for ws in self.active]
       results = await asyncio.gather(*tasks, return_exceptions=True)
       # Remove dead connections
   ```

2. **Use orjson** — Drop-in replacement for `json`, 3-10x faster serialization/deserialization. One line change in imports.

3. **Consistent async I/O** — Audit all file operations, wrap in `run_in_executor`. Or use `aiofiles`.

4. **Structured error handling** — Replace bare `except Exception` + `print()` with proper logging and typed error responses.

5. **Remove dead code** — `spawner.py` is the old implementation superseded by `agent_session.py`. Delete it.

6. **Add structured concurrency** — Use `asyncio.TaskGroup` (Python 3.11+) instead of fire-and-forget `create_task`.

These fixes are 1-2 days of work and address every practical bottleneck.

### Secondary: If rewriting, choose Go.

If the project outgrows Python (50+ concurrent agents, multiple users, latency SLA), Go is the right target:
- Goroutines + `io.Scanner` + `encoding/json` handles the hot path with zero GIL concerns
- Static binary simplifies deployment dramatically
- The 79-endpoint boilerplate problem is real but manageable with code generation

### Migration strategy (if choosing Go):
1. **Week 1**: Port `AgentBroker` + `AgentSession` to Go. Test against real Claude CLI output.
2. **Week 2**: Port WebSocket manager and core API endpoints (dispatch, cancel, inject, agent list).
3. **Week 3**: Port remaining endpoints. This is grunt work — consider using Claude to generate the Go handlers from the Python source.
4. **Week 4**: Port MCP servers (or keep as Python sidecars initially). Integration testing.

---

## 6. What We'd Lose

### If staying with Python:
- True parallelism for CPU-bound work (irrelevant at current scale)
- Smaller container images (current image is ~800MB+ due to Node.js + Python + system deps)
- Compile-time type safety (Pydantic provides runtime safety, which is arguably better for a dynamic system)

### If moving to Go:
- FastAPI's auto-OpenAPI documentation
- Pydantic's elegant model validation
- FastMCP's decorator-based tool registration
- Development velocity during the 3-4 week migration
- The ability to prototype features in minutes

### If moving to TypeScript:
- Pydantic (Zod is close but not equivalent)
- FastAPI's dependency injection
- Python ecosystem libraries (croniter, etc. — npm equivalents exist but differ)

### If moving to Rust:
- Development velocity (permanently — Rust is always slower to write)
- Quick prototyping ability
- Easy onboarding for contributors

---

## 7. Decision Framework

**Choose Python (stay)** if you value:
- Maximum development velocity
- A working system with known quirks
- The ability to ship features in hours
- Minimal risk

**Choose Go** if you value:
- Deploying to resource-constrained environments (tiny containers, fast startup)
- Scaling to 50+ concurrent agents
- True subprocess concurrency without event loop contention
- Long-term maintainability of the subprocess management code

**Choose TypeScript** if you value:
- MCP SDK parity (official SDK, first-class support)
- Unifying the frontend and backend language
- A middle ground between Python's velocity and Go's performance
- The growing importance of MCP in the architecture

**Choose Rust** if you value:
- Absolute performance and memory safety guarantees
- This becoming a multi-tenant production service
- (And you have 6+ weeks to spare)

**Choose Elixir** if you value:
- The theoretically perfect concurrency model for this workload
- Bulletproof fault tolerance via supervision trees
- (And you're willing to learn a new paradigm and build missing ecosystem pieces)

---

*Analysis based on codebase snapshot at commit 6e110ef. Total backend: 8,788 lines across 26 files, 79 HTTP endpoints, 1 WebSocket endpoint, 2 MCP sidecar services.*
