# IDE / Code Editor Integration Research

> Research conducted 2026-03-05 for claude-manager dashboard.

## Current State

The Artifacts tab (`ArtifactsPanel.js`) provides:
- Split-pane file tree (left) + read-only preview (right)
- Syntax highlighting via highlight.js (CDN)
- Git status badges per file
- Backend endpoints: `GET /api/projects/{name}/files` (list), `GET /api/projects/{name}/files/content` (read), `GET /api/projects/{name}/files/status` (git status)
- No write endpoint exists yet

---

## Option 1: Monaco Editor (VS Code's editor engine)

**Recommendation: Best option for our use case.**

### What It Is
Monaco is the editor component extracted from VS Code. It provides syntax highlighting, IntelliSense, multi-cursor, find/replace, code folding, minimap, diff view, and support for ~70 languages out of the box.

### CDN Loading (vanilla JS, no bundler needed)

Monaco uses AMD modules. Load via CDN with the built-in loader:

```html
<!-- In index.html -->
<script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js"></script>
```

```javascript
// In your JS module
function loadMonaco() {
  return new Promise((resolve) => {
    require.config({
      paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs' }
    });
    // Needed for web workers to load from CDN
    window.MonacoEnvironment = {
      getWorkerUrl: function (workerId, label) {
        return `data:text/javascript;charset=utf-8,${encodeURIComponent(`
          self.MonacoEnvironment = { baseUrl: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/' };
          importScripts('https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/base/worker/workerMain.js');
        `)}`;
      }
    };
    require(['vs/editor/editor.main'], function () {
      resolve(window.monaco);
    });
  });
}
```

### Multi-File Tabs with Model Swapping

Monaco natively supports multiple "models" (file buffers). Each model preserves its own undo stack, cursor position, scroll position, and language mode. You swap the visible file by calling `editor.setModel(model)`:

```javascript
class EditorPanel {
  constructor(container, projectName) {
    this._project = projectName;
    this._models = new Map();      // path -> monaco.editor.ITextModel
    this._viewStates = new Map();  // path -> editor.saveViewState()
    this._activeFile = null;
    this._dirty = new Set();       // paths with unsaved changes
    this._tabs = [];
    this._container = container;
    this._editor = null;
  }

  async init() {
    const monaco = await loadMonaco();
    // Create editor with VS Code dark theme
    this._editor = monaco.editor.create(this._container.querySelector('.editor-area'), {
      theme: 'vs-dark',
      fontSize: 13,
      fontFamily: "'JetBrains Mono', 'IBM Plex Mono', monospace",
      minimap: { enabled: true },
      automaticLayout: true,    // auto-resize with container
      scrollBeyondLastLine: false,
      wordWrap: 'on',
      tabSize: 2,
      renderWhitespace: 'selection',
      bracketPairColorization: { enabled: true },
    });

    // Ctrl+S / Cmd+S to save
    this._editor.addCommand(
      monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
      () => this._saveActiveFile()
    );

    // Track dirty state
    this._editor.onDidChangeModelContent(() => {
      if (this._activeFile) {
        this._dirty.add(this._activeFile);
        this._updateTabDirtyState(this._activeFile);
      }
    });
  }

  async openFile(path) {
    if (!this._models.has(path)) {
      // Fetch content from backend
      const result = await api.readFile(this._project, path);
      if (result.binary) return; // skip binary

      const ext = '.' + path.split('.').pop().toLowerCase();
      const lang = EXT_TO_MONACO_LANG[ext] || 'plaintext';
      const uri = monaco.Uri.parse(`file:///${this._project}/${path}`);
      const model = monaco.editor.createModel(result.content, lang, uri);
      this._models.set(path, model);
      this._addTab(path);
    }

    // Save current view state
    if (this._activeFile) {
      this._viewStates.set(this._activeFile, this._editor.saveViewState());
    }

    // Switch to new model
    this._activeFile = path;
    this._editor.setModel(this._models.get(path));

    // Restore view state if exists
    const viewState = this._viewStates.get(path);
    if (viewState) this._editor.restoreViewState(viewState);
    this._editor.focus();
    this._highlightActiveTab(path);
  }

  async _saveActiveFile() {
    if (!this._activeFile || !this._dirty.has(this._activeFile)) return;
    const model = this._models.get(this._activeFile);
    const content = model.getValue();
    await api.writeFile(this._project, this._activeFile, content);
    this._dirty.delete(this._activeFile);
    this._updateTabDirtyState(this._activeFile);
  }

  closeFile(path) {
    const model = this._models.get(path);
    if (model) model.dispose();
    this._models.delete(path);
    this._viewStates.delete(path);
    this._dirty.delete(path);
    this._removeTab(path);
    // Switch to another open file or show empty state
  }

  destroy() {
    this._models.forEach(m => m.dispose());
    this._editor?.dispose();
  }
}
```

### IntelliSense / Autocomplete

Monaco ships with built-in IntelliSense for JavaScript, TypeScript, JSON, CSS, and HTML via web workers. For other languages:

- **Basic autocomplete**: Register a `CompletionItemProvider` for any language with static or dynamic suggestions.
- **Full LSP**: Use [monaco-languageclient](https://github.com/TypeFox/monaco-languageclient) to connect to a Language Server over WebSocket. This would require deploying language servers (e.g., pylsp for Python) in the cluster — significant effort but possible.
- **Practical recommendation**: The built-in JS/TS/CSS/JSON IntelliSense is excellent with zero config. For Python and other languages, basic keyword autocomplete is sufficient for an agent orchestration tool.

### Themes

Monaco ships with three built-in themes: `vs`, `vs-dark`, `hc-black`. You can also define custom themes:

```javascript
monaco.editor.defineTheme('void-dark', {
  base: 'vs-dark',
  inherit: true,
  rules: [
    { token: 'comment', foreground: '6a9955' },
    { token: 'keyword', foreground: '67e8f9' },  // cyan accent
    { token: 'string', foreground: 'c084fc' },   // purple accent
  ],
  colors: {
    'editor.background': '#0a0a12',
    'editor.foreground': '#d4d4d4',
    'editor.lineHighlightBackground': '#ffffff08',
    'editorCursor.foreground': '#67e8f9',
    'editor.selectionBackground': '#67e8f920',
  }
});
```

### Diff View

Monaco has a built-in diff editor — useful for showing git changes:

```javascript
const diffEditor = monaco.editor.createDiffEditor(container, { theme: 'vs-dark' });
diffEditor.setModel({
  original: monaco.editor.createModel(originalContent, lang),
  modified: monaco.editor.createModel(modifiedContent, lang),
});
```

### Size / Performance

| Metric | Value |
|--------|-------|
| CDN download (gzipped) | ~2.1 MB for core + worker |
| CDN download (all languages) | ~5 MB |
| Initial parse time | ~200-400ms |
| Memory per model | ~1-3 MB depending on file size |
| Web workers | 1 main + language-specific (JS/TS, CSS, JSON, HTML) |

This is acceptable. The CDN is cached after first load. The app already loads GridStack, highlight.js, marked.js, and DOMPurify from CDN — Monaco is heavier but justified for a code editor.

### Backend Write Endpoint Needed

```python
# Add to backend/main.py
@app.put("/api/projects/{name}/files/content")
async def write_project_file(name: str, request: WriteFileRequest) -> dict:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, artifacts_svc.write_file, name, request.path, request.content)
    return {"status": "ok", "path": request.path}
```

```python
# Add to backend/services/artifacts.py
def write_file(project_name: str, path: str, content: str) -> None:
    """Write content to a file within a project (text files only)."""
    target = _safe_path(project_name, path)
    # Safety: only allow writing to existing files or new files in existing directories
    if not target.parent.exists():
        raise ValueError(f"Parent directory does not exist: {path}")
    ext = target.suffix.lower()
    if ext in _BINARY_EXTENSIONS:
        raise ValueError(f"Cannot write binary file: {path}")
    target.write_text(content, encoding="utf-8")
```

```javascript
// Add to frontend/js/api.js
async writeFile(projectName, path, content) {
  const url = `${BASE}/api/projects/${encodeURIComponent(projectName)}/files/content`;
  const res = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, content }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
},
```

### Implementation Complexity: 2/5

Monaco is well-documented, CDN-ready, and fits perfectly in a vanilla JS app. The multi-file tab system requires custom UI but the editor mechanics are handled by Monaco's model system.

---

## Option 2: CodeMirror 6

### What It Is
CodeMirror 6 is a complete rewrite of CodeMirror, built with ES modules, highly modular, and significantly smaller than Monaco.

### CDN Loading

CodeMirror 6 uses ES modules. You can load via esm.sh or skypack:

```html
<script type="importmap">
{
  "imports": {
    "codemirror": "https://esm.sh/codemirror@6",
    "@codemirror/lang-javascript": "https://esm.sh/@codemirror/lang-javascript@6",
    "@codemirror/lang-python": "https://esm.sh/@codemirror/lang-python@6",
    "@codemirror/theme-one-dark": "https://esm.sh/@codemirror/theme-one-dark@6"
  }
}
</script>
```

**Problem**: Import maps work in modern browsers, but CodeMirror 6 has many transitive dependencies (`@lezer/common`, `@codemirror/state`, `@codemirror/view`, etc.) that all need mapping. In practice, you either need a bundler or a CDN that resolves dependencies automatically (esm.sh does this, but adds latency for cold loads). This is messier than Monaco's single AMD loader.

### Size Advantage

| Metric | CodeMirror 6 | Monaco |
|--------|-------------|--------|
| Core (gzipped) | ~124 KB | ~2.1 MB |
| With all lang packs | ~1.3 MB | ~5 MB |

CodeMirror wins on size, but 2 MB (cached from CDN) is not a dealbreaker.

### Multi-File Tabs

CodeMirror has no built-in multi-file model system. You must manually manage:
- Create/destroy `EditorState` per file
- Store/restore scroll position and selection
- Build your own tab bar and switching logic
- Manage undo history per file

This is more manual work than Monaco's `createModel` / `setModel` approach.

### Themes

CodeMirror supports custom themes via `EditorView.theme()`. The `@codemirror/theme-one-dark` package provides a good dark theme. Custom theming is more CSS-like and flexible but requires more setup.

### IntelliSense

CodeMirror has `@codemirror/autocomplete` for basic completion, but nothing approaching Monaco's built-in JS/TS IntelliSense. You'd need to build completion sources manually.

### Diff View

No built-in diff editor. Would need a separate library (e.g., `diff-match-patch` + custom rendering).

### Implementation Complexity: 3/5

Smaller footprint but more assembly required. The CDN story is worse (import maps + transitive deps). No built-in multi-model, no built-in diff view, weaker IntelliSense. Best suited for projects that already use a bundler and want minimal size.

### Verdict: Not recommended over Monaco for this project.

---

## Option 3: code-server (Coder)

### What It Is
code-server runs the full VS Code application in the browser. It's a Node.js server that serves VS Code's web client and handles file system, terminal, extensions, and everything else.

### Embedding in an iframe

```html
<iframe src="https://code-server.localhost/?folder=/projects/my-project"
        style="width:100%; height:100%; border:none;"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals">
</iframe>
```

**Caveat**: code-server sets `X-Frame-Options: SAMEORIGIN` by default. You'd need to configure it to allow framing or run it on the same origin.

### Kubernetes Deployment

```yaml
# Helm values for code-server
image:
  repository: codercom/code-server
  tag: "4.96.4"
persistence:
  enabled: true
  # Mount managed projects directory via hostPath
  existingClaim: ""
extraVolumeMounts:
  - name: projects
    mountPath: /projects
    readOnly: false
extraVolumes:
  - name: projects
    hostPath:
      path: /home/user/git/claude-managed-projects  # bind-mounted into kind nodes
      type: Directory
ingress:
  enabled: true
  hosts:
    - host: code-server.localhost
      paths:
        - /
```

### Pointing at Different Projects

You'd pass the `?folder=/projects/<name>` query parameter. When the user selects a project in claude-manager, the iframe URL updates to point at that project's folder.

### Authentication

code-server supports password auth (default), no auth (`--auth none`), or proxy authentication. Since it's behind the kind cluster's NGINX ingress and only accessible locally, `--auth none` is simplest.

### What You Get

- Full VS Code: extensions, settings sync, terminal, debugger, Git UI, search, everything
- No custom code needed for the editor itself
- Extensions marketplace (Open VSX, not the official Microsoft marketplace)
- Built-in terminal (though you already have terminal widgets)

### What You Lose

- Deep integration with claude-manager's UI (it's a black box in an iframe)
- No real-time file change events pushed to your WebSocket
- Separate deployment to maintain
- Heavy resource usage (~300-500 MB RAM per instance)
- Harder to theme to match your dashboard aesthetic

### Implementation Complexity: 3/5

Deploying code-server itself is straightforward with Helm. But integrating it into the dashboard (iframe communication, project switching, matching the UI theme) is awkward. It's the "nuclear option" — maximum capability, minimum integration.

---

## Option 4: OpenVSCode Server (Gitpod)

### What It Is
OpenVSCode Server is Gitpod's fork of VS Code that runs in the browser. Unlike code-server (which patches VS Code), OpenVSCode Server commits changes directly to its fork of VS Code.

### Key Differences from code-server

| Aspect | code-server | OpenVSCode Server |
|--------|------------|-------------------|
| Architecture | Patches VS Code via submodule | Direct fork of VS Code |
| Extra features | Better self-hosted UX (auth, proxy) | Closer to upstream VS Code |
| Extension install | CLI + marketplace | No non-interactive install (limitation) |
| Maintenance | Active (Coder) | Active (Gitpod) |

### Kubernetes Deployment

A Helm chart is available on ArtifactHub: `nimtechnology/openvscode-server-helm`. The deployment pattern is identical to code-server — hostPath volume mount for the projects directory.

### Verdict

OpenVSCode Server is functionally equivalent to code-server for our use case. code-server has better documentation, more Kubernetes deployment guides, and better self-hosted features (auth, proxy support). Choose code-server if going the iframe route.

### Implementation Complexity: 3/5

Same as code-server. No meaningful advantage over code-server for this project.

---

## Comparison Matrix

| Criterion | Monaco Editor | CodeMirror 6 | code-server | OpenVSCode Server |
|-----------|:------------:|:------------:|:-----------:|:-----------------:|
| **Implementation complexity** | 2/5 | 3/5 | 3/5 | 3/5 |
| **CDN/bundle size** | ~2 MB (gzip) | ~124 KB core | N/A (server) | N/A (server) |
| **UI integration** | Seamless | Seamless | iframe (poor) | iframe (poor) |
| **Read + Write** | Via API | Via API | Built-in | Built-in |
| **Multi-file tabs** | Native (models) | Manual | Built-in | Built-in |
| **IntelliSense** | JS/TS/CSS/JSON built-in | Basic only | Full VS Code | Full VS Code |
| **Diff view** | Built-in | None | Built-in | Built-in |
| **Git integration** | Via custom UI | Via custom UI | Built-in (SCM) | Built-in (SCM) |
| **Terminal** | No (use existing widgets) | No | Built-in | Built-in |
| **Custom theming** | Good (defineTheme) | Excellent (CSS) | Poor (iframe) | Poor (iframe) |
| **Resource cost** | ~5 MB CDN (cached) | ~1.3 MB CDN | ~500 MB RAM server | ~500 MB RAM server |
| **Matches dashboard aesthetic** | Yes | Yes | No | No |
| **Maintenance burden** | Low (CDN) | Low (CDN) | Medium (k8s deploy) | Medium (k8s deploy) |

---

## Recommendation: Monaco Editor

**Monaco is the clear winner** for these reasons:

1. **Seamless integration** — it renders inside our DOM, uses our CSS variables, matches our dark/space aesthetic. No iframe boundary.
2. **Native multi-file model system** — built-in undo/redo per file, cursor/scroll state preservation, model swapping. This is the killer feature vs. CodeMirror.
3. **Built-in IntelliSense** for JS/TS/CSS/JSON — covers the most common languages in managed projects.
4. **Built-in diff editor** — can show git diffs inline.
5. **CDN-ready** — single AMD loader script, no bundler changes needed. Works perfectly in our Vite SPA (Vite won't interfere with AMD).
6. **Well-proven** — used by VS Code, GitHub, GitLab, Azure DevOps, and hundreds of other products.

The 2 MB CDN cost is acceptable and cached after first load.

---

## Where Should It Live?

### Option A: New "Editor" Tab in FeedController

Replace the current Artifacts tab with a full editor tab. Keep the file tree on the left, but swap highlight.js preview for Monaco editor on the right.

**Pros**: Natural upgrade path from Artifacts. File tree is already built.
**Cons**: Constrained by the feed panel width.

### Option B: Canvas Widget

Build the editor as a canvas widget that can be placed and resized on the dashboard.

**Pros**: Flexible layout — user can have editor + terminal + constellation side by side.
**Cons**: Canvas widgets run in iframes via `new Function()` — Monaco's AMD loader may conflict. Widget lifecycle (create/destroy) adds complexity.

### Option C: Standalone Full-Screen Page

A new top-level view (like Canvas/Settings) accessible from the header nav.

**Pros**: Maximum screen real estate. Clean separation.
**Cons**: Disconnected from the project feed context.

### Recommendation: Option A (Upgrade Artifacts Tab)

Upgrade the existing Artifacts tab to become an "Editor" tab. The split-pane layout is already there — swap the right-side preview from highlight.js `<pre><code>` to a Monaco editor instance. Add a tab bar above the editor for open files. Add Ctrl+S save. This is the lowest-friction path and keeps the editor contextual to the selected project.

Later, consider also offering it as a standalone page for full-screen editing sessions.

---

## Implementation Plan

### Phase 1: Backend Write Endpoint (30 min)

1. Add `WriteFileRequest` model to `backend/models.py`:
   ```python
   class WriteFileRequest(BaseModel):
       path: str
       content: str
   ```
2. Add `write_file()` to `backend/services/artifacts.py` (with path traversal protection, binary check).
3. Add `PUT /api/projects/{name}/files/content` to `backend/main.py`.
4. Add `writeFile()` to `frontend/js/api.js`.

### Phase 2: Monaco Integration (2-3 hours)

1. Add Monaco AMD loader script to `index.html`:
   ```html
   <script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js"></script>
   ```
2. Create `frontend/js/feed/EditorPanel.js` (replaces ArtifactsPanel):
   - Reuse file tree from ArtifactsPanel (copy or extend)
   - Add tab bar above editor area
   - Initialize Monaco with `vs-dark` theme
   - Multi-file model management (open, switch, close tabs)
   - Ctrl+S / Cmd+S save binding
   - Dirty indicator (dot on tab)
   - MonacoEnvironment worker configuration for CDN
3. Update `FeedController.js`:
   - Replace `ArtifactsPanel` import with `EditorPanel`
   - Rename tab label from "Artifacts" to "Editor"

### Phase 3: Custom Theme + Polish (1 hour)

1. Define `void-dark` Monaco theme matching the dashboard aesthetic (dark bg, cyan/purple accents).
2. Style the tab bar and file tree to match existing CSS.
3. Add file-type icons to tabs.
4. Add "unsaved changes" warning on tab close / project switch.

### Phase 4: Git Integration (1-2 hours)

1. Add diff view mode — toggle button to show Monaco diff editor for modified files.
2. Use existing `GET /api/projects/{name}/files/status` to mark modified files in tree and tabs.
3. Add "Revert" action (re-fetch original content from backend).

### Phase 5: Optional Enhancements

- **Search across files**: Add a "Find in Files" panel using a backend grep endpoint.
- **File creation/deletion**: Add `POST /api/projects/{name}/files` and `DELETE /api/projects/{name}/files`.
- **Rename/move**: Add `PATCH /api/projects/{name}/files`.
- **Real-time updates**: WebSocket event when an agent modifies a file — auto-reload model if not dirty.
- **LSP integration**: Deploy language servers in the cluster, connect via WebSocket using `monaco-languageclient` for Python/Go/Rust IntelliSense.

---

## Estimated Total Effort

| Phase | Time | Priority |
|-------|------|----------|
| Phase 1: Backend write endpoint | 30 min | Required |
| Phase 2: Monaco + tabs + save | 2-3 hours | Required |
| Phase 3: Theme + polish | 1 hour | Required |
| Phase 4: Git diff view | 1-2 hours | Nice-to-have |
| Phase 5: Advanced features | Ongoing | Future |
| **Total MVP** | **~4-5 hours** | |

---

## Sources

- [Monaco Editor Official](https://microsoft.github.io/monaco-editor/)
- [Monaco CDN via cdnjs](https://cdnjs.com/libraries/monaco-editor)
- [Monaco CDN Setup Guide](https://www.codestudy.net/blog/how-to-run-the-monaco-editor-from-a-cdn-like-cdnjs/)
- [Monaco CDN Embedding](https://log.schemescape.com/posts/web-development/embedding-monaco-from-cdn.html)
- [Monaco Multi-File Tabs (GitHub Issue #604)](https://github.com/microsoft/monaco-editor/issues/604)
- [monaco-languageclient](https://github.com/TypeFox/monaco-languageclient)
- [CodeMirror vs Monaco Comparison](https://agenthicks.com/research/codemirror-vs-monaco-editor-comparison)
- [Sourcegraph: Migrating Monaco to CodeMirror](https://sourcegraph.com/blog/migrating-monaco-codemirror)
- [CodeMirror ESM CDN Discussion](https://discuss.codemirror.net/t/esm-compatible-codemirror-build-directly-importable-in-browser/5933)
- [code-server Helm Chart](https://coder.com/docs/code-server/helm)
- [code-server vs OpenVSCode Server Discussion](https://github.com/coder/code-server/discussions/4267)
- [OpenVSCode Server GitHub](https://github.com/gitpod-io/openvscode-server)
- [Replit: Betting on CodeMirror](https://blog.replit.com/codemirror)
