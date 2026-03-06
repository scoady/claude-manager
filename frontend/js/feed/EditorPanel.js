/** EditorPanel — VS Code-style IDE with Monaco Editor, file tree, and terminal output. */
import { escapeHtml } from '../utils.js';
import { api } from '../api.js';
import { toast } from '../utils.js';

// ── File extension to Monaco language mapping ──────────────────────────────
const EXT_LANG = {
  '.js': 'javascript', '.mjs': 'javascript', '.jsx': 'javascript', '.cjs': 'javascript',
  '.ts': 'typescript', '.tsx': 'typescript',
  '.py': 'python', '.pyw': 'python',
  '.rb': 'ruby', '.rs': 'rust', '.go': 'go',
  '.java': 'java', '.kt': 'kotlin', '.kts': 'kotlin',
  '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.cc': 'cpp', '.hpp': 'cpp',
  '.cs': 'csharp', '.swift': 'swift',
  '.html': 'html', '.htm': 'html',
  '.xml': 'xml', '.svg': 'xml', '.xsl': 'xml',
  '.css': 'css', '.scss': 'scss', '.less': 'less',
  '.json': 'json', '.jsonc': 'json',
  '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'ini',
  '.md': 'markdown', '.mdx': 'markdown',
  '.sh': 'shell', '.bash': 'shell', '.zsh': 'shell',
  '.sql': 'sql', '.graphql': 'graphql',
  '.dockerfile': 'dockerfile',
  '.r': 'r', '.lua': 'lua', '.php': 'php', '.pl': 'perl',
  '.tf': 'hcl', '.ini': 'ini', '.cfg': 'ini',
  '.vue': 'html', '.svelte': 'html',
  '.env': 'ini', '.gitignore': 'plaintext',
  '.lock': 'json',
};

// ── Tab accent colors by file type ─────────────────────────────────────────
const TAB_ACCENT = {
  javascript: '#67e8f9', typescript: '#67e8f9',
  json: '#fbbf24', yaml: '#fbbf24', ini: '#fbbf24',
  python: '#4ade80', shell: '#4ade80',
  css: '#c084fc', scss: '#c084fc', less: '#c084fc',
  html: '#f9a8d4', xml: '#f9a8d4',
  rust: '#f87171', ruby: '#f87171',
  go: '#5eead4',
  markdown: '#a78bfa',
  sql: '#60a5fa',
};

// ── Git status labels ──────────────────────────────────────────────────────
const GIT_LABELS = {
  'M': { cls: 'ide-git-modified' },
  'A': { cls: 'ide-git-added' },
  'D': { cls: 'ide-git-deleted' },
  '?': { cls: 'ide-git-untracked' },
  '??': { cls: 'ide-git-untracked' },
  'R': { cls: 'ide-git-renamed' },
};

let _monacoLoaded = false;
let _monacoLoadPromise = null;

function loadMonaco() {
  if (_monacoLoaded) return Promise.resolve();
  if (_monacoLoadPromise) return _monacoLoadPromise;

  _monacoLoadPromise = new Promise((resolve, reject) => {
    // Load AMD loader
    if (typeof require !== 'undefined' && typeof require.config === 'function') {
      _configAndLoad(resolve, reject);
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js';
    script.onload = () => _configAndLoad(resolve, reject);
    script.onerror = reject;
    document.head.appendChild(script);
  });

  return _monacoLoadPromise;
}

function _configAndLoad(resolve, reject) {
  require.config({
    paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs' },
  });
  require(['vs/editor/editor.main'], () => {
    _monacoLoaded = true;
    _defineStarshipTheme();
    resolve();
  }, reject);
}

function _defineStarshipTheme() {
  monaco.editor.defineTheme('starship-void', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: '', foreground: 'c9d1d9' },
      { token: 'comment', foreground: '4a5568', fontStyle: 'italic' },
      { token: 'keyword', foreground: 'c084fc' },
      { token: 'string', foreground: '67e8f9' },
      { token: 'number', foreground: 'fbbf24' },
      { token: 'type', foreground: '5eead4' },
      { token: 'function', foreground: '60a5fa' },
      { token: 'variable', foreground: 'e2e8f0' },
      { token: 'operator', foreground: 'f9a8d4' },
      { token: 'delimiter', foreground: '94a3b8' },
      { token: 'tag', foreground: 'f87171' },
      { token: 'attribute.name', foreground: 'fbbf24' },
      { token: 'attribute.value', foreground: '67e8f9' },
      { token: 'metatag', foreground: 'c084fc' },
      { token: 'regexp', foreground: 'f9a8d4' },
      { token: 'annotation', foreground: 'fbbf24' },
      { token: 'constant', foreground: 'f87171' },
    ],
    colors: {
      'editor.background': '#0a0a1a',
      'editor.foreground': '#c9d1d9',
      'editor.lineHighlightBackground': '#111133',
      'editor.selectionBackground': '#4c1d9544',
      'editor.inactiveSelectionBackground': '#4c1d9522',
      'editorLineNumber.foreground': '#3a506b',
      'editorLineNumber.activeForeground': '#67e8f9',
      'editorCursor.foreground': '#67e8f9',
      'editorWhitespace.foreground': '#1a2640',
      'editorIndentGuide.background': '#1a2640',
      'editorIndentGuide.activeBackground': '#2d3f5f',
      'editor.selectionHighlightBackground': '#c084fc15',
      'editor.wordHighlightBackground': '#67e8f915',
      'editorBracketMatch.background': '#67e8f920',
      'editorBracketMatch.border': '#67e8f940',
      'editorGutter.background': '#0a0a1a',
      'minimap.background': '#0a0a1a',
      'minimapSlider.background': '#67e8f915',
      'minimapSlider.hoverBackground': '#67e8f925',
      'minimapSlider.activeBackground': '#67e8f935',
      'scrollbarSlider.background': '#243352',
      'scrollbarSlider.hoverBackground': '#2d3f5f',
      'scrollbarSlider.activeBackground': '#3a506b',
      'editorOverviewRuler.border': '#0a0a1a',
      'editorWidget.background': '#0e1525',
      'editorWidget.border': '#243352',
      'editorSuggestWidget.background': '#0e1525',
      'editorSuggestWidget.border': '#243352',
      'editorSuggestWidget.selectedBackground': '#1a2640',
      'list.hoverBackground': '#1a2640',
      'list.focusBackground': '#1a2640',
    },
  });
}

export class EditorPanel {
  constructor(projectName) {
    this._project = projectName;
    this._gitStatus = {};
    this._gitBranch = '';
    this._expandedPaths = new Set();
    this._treeCache = new Map();
    this._selectedFile = null;

    // Multi-tab state
    this._openTabs = []; // [{path, lang, content, model, viewState, dirty}]
    this._activeTabPath = null;
    this._editor = null;
    this._editorReady = false;

    // Bottom panel
    this._bottomVisible = false;
    this._bottomOutput = [];

    // Resize state
    this._sidebarWidth = 240;
    this._bottomHeight = 180;

    this._el = document.createElement('div');
    this._el.className = 'ide-panel';
    this._render();
  }

  get el() { return this._el; }

  async load() {
    this._logOutput('Initializing editor...');
    try {
      const [gitStatus, branchInfo] = await Promise.all([
        api.getGitStatus(this._project).catch(() => ({})),
        api.getGitBranch(this._project).catch(() => ({ branch: '' })),
      ]);
      this._gitStatus = gitStatus;
      this._gitBranch = branchInfo.branch || '';
      this._updateBranchIndicator();
    } catch (_) {}

    await this._loadDirectory('');
    this._renderTree();
    this._logOutput('Editor ready. Open a file from the explorer.');

    // Load Monaco in background
    try {
      await loadMonaco();
      this._logOutput('Monaco Editor loaded.');
    } catch (e) {
      this._logOutput(`Failed to load Monaco: ${e.message}`);
    }
  }

  destroy() {
    if (this._editor) {
      this._editor.dispose();
      this._editor = null;
    }
    this._openTabs.forEach(tab => {
      if (tab.model) tab.model.dispose();
    });
    this._openTabs = [];
    document.removeEventListener('keydown', this._saveHandler);
  }

  // ── Layout rendering ───────────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="ide-layout">
        <div class="ide-sidebar" style="width:${this._sidebarWidth}px">
          <div class="ide-sidebar-header">
            <span class="ide-sidebar-title">EXPLORER</span>
          </div>
          <div class="ide-file-tree" role="tree"></div>
          <div class="ide-sidebar-resize"></div>
        </div>
        <div class="ide-main">
          <div class="ide-toolbar">
            <div class="ide-breadcrumb"></div>
            <div class="ide-toolbar-right">
              <span class="ide-branch-indicator">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                  <circle cx="5" cy="4" r="2" stroke="currentColor" stroke-width="1.3"/>
                  <circle cx="5" cy="12" r="2" stroke="currentColor" stroke-width="1.3"/>
                  <circle cx="11" cy="8" r="2" stroke="currentColor" stroke-width="1.3"/>
                  <path d="M5 6v4M7 4h2c1.1 0 2 .9 2 2v2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
                </svg>
                <span class="ide-branch-name"></span>
              </span>
              <button class="ide-save-btn" title="Save (Cmd+S)">
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                  <path d="M10.5 11.5H2.5a1 1 0 01-1-1V2.5a1 1 0 011-1h6.59a1 1 0 01.7.29l1.92 1.92a1 1 0 01.29.7V10.5a1 1 0 01-1 1z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>
                  <path d="M4.5 1.5v3h4v-3M4.5 8h4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
                </svg>
                Save
              </button>
            </div>
          </div>
          <div class="ide-tab-bar"></div>
          <div class="ide-editor-area">
            <div class="ide-editor-welcome">
              <div class="ide-welcome-icon">
                <svg width="48" height="48" viewBox="0 0 48 48" fill="none" opacity="0.4">
                  <path d="M15 12l-9 12 9 12M33 12l9 12-9 12M28 8L20 40" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </div>
              <span class="ide-welcome-title">Stellar Code Editor</span>
              <span class="ide-welcome-hint">Open a file from the explorer to start editing</span>
              <div class="ide-welcome-shortcuts">
                <span><kbd>Cmd+S</kbd> Save file</span>
                <span><kbd>Cmd+P</kbd> Quick open</span>
                <span><kbd>Cmd+W</kbd> Close tab</span>
              </div>
            </div>
          </div>
          <div class="ide-bottom-toggle">
            <button class="ide-bottom-toggle-btn">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M1 4h10M1 8h10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
              </svg>
              Terminal Output
            </button>
          </div>
          <div class="ide-bottom-panel ${this._bottomVisible ? '' : 'collapsed'}" style="height:${this._bottomHeight}px">
            <div class="ide-bottom-resize"></div>
            <div class="ide-bottom-header">
              <span class="ide-bottom-title">OUTPUT</span>
              <button class="ide-bottom-clear" title="Clear">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
                </svg>
              </button>
            </div>
            <div class="ide-bottom-content"></div>
            <div class="ide-scanline-overlay"></div>
          </div>
        </div>
      </div>
    `;

    this._bindEvents();
  }

  _bindEvents() {
    // Sidebar resize
    const sidebarResize = this._el.querySelector('.ide-sidebar-resize');
    if (sidebarResize) {
      sidebarResize.addEventListener('mousedown', (e) => this._startSidebarResize(e));
    }

    // Bottom panel toggle
    const toggleBtn = this._el.querySelector('.ide-bottom-toggle-btn');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => this._toggleBottom());
    }

    // Bottom resize
    const bottomResize = this._el.querySelector('.ide-bottom-resize');
    if (bottomResize) {
      bottomResize.addEventListener('mousedown', (e) => this._startBottomResize(e));
    }

    // Bottom clear
    const clearBtn = this._el.querySelector('.ide-bottom-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        this._bottomOutput = [];
        this._renderBottomContent();
      });
    }

    // Save button
    const saveBtn = this._el.querySelector('.ide-save-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', () => this._saveActiveFile());
    }

    // Keyboard shortcut: Cmd+S
    this._saveHandler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        this._saveActiveFile();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === 'w') {
        if (this._activeTabPath && this._el.contains(document.activeElement)) {
          e.preventDefault();
          this._closeTab(this._activeTabPath);
        }
      }
    };
    document.addEventListener('keydown', this._saveHandler);
  }

  // ── File tree ──────────────────────────────────────────────────────────────

  async _loadDirectory(path) {
    try {
      const files = await api.listFiles(this._project, path);
      this._treeCache.set(path, files);
      return files;
    } catch (e) {
      this._logOutput(`Error loading directory: ${e.message}`);
      return [];
    }
  }

  _renderTree() {
    const treeEl = this._el.querySelector('.ide-file-tree');
    if (!treeEl) return;
    const rootFiles = this._treeCache.get('') || [];
    treeEl.innerHTML = this._buildTreeHtml(rootFiles, 0);
    this._bindTreeEvents(treeEl);
  }

  _buildTreeHtml(entries, depth) {
    return entries.map(entry => {
      const isDir = entry.type === 'directory';
      const isExpanded = this._expandedPaths.has(entry.path);
      const isSelected = this._selectedFile === entry.path;
      const isOpen = this._openTabs.some(t => t.path === entry.path);
      const gitCls = this._getGitClass(entry.path, isDir);

      const chevron = isDir
        ? `<span class="ide-chevron ${isExpanded ? 'open' : ''}">
            <svg width="8" height="8" viewBox="0 0 8 8"><path d="M2 1l4 3-4 3" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </span>`
        : '<span class="ide-chevron-spacer"></span>';

      const icon = this._getFileIcon(entry.name, isDir);

      let children = '';
      if (isDir && isExpanded) {
        const childEntries = this._treeCache.get(entry.path) || [];
        children = `<div class="ide-tree-children">${this._buildTreeHtml(childEntries, depth + 1)}</div>`;
      }

      return `
        <div class="ide-tree-node${isSelected ? ' selected' : ''}${isOpen ? ' open-file' : ''}${gitCls ? ' ' + gitCls : ''}" data-path="${escapeHtml(entry.path)}" data-type="${entry.type}" style="--depth:${depth}">
          ${chevron}
          <span class="ide-file-icon">${icon}</span>
          <span class="ide-file-name">${escapeHtml(entry.name)}</span>
          ${this._getGitDot(entry.path, isDir)}
        </div>
        ${children}
      `;
    }).join('');
  }

  _getFileIcon(name, isDir) {
    if (isDir) return '<span class="ide-icon-dir"></span>';
    const ext = '.' + name.split('.').pop().toLowerCase();
    const lang = EXT_LANG[ext] || '';
    const color = TAB_ACCENT[lang] || '#94a3b8';
    return `<span class="ide-icon-file" style="--icon-color:${color}"></span>`;
  }

  _getGitClass(path, isDir) {
    if (isDir) {
      const hasChange = Object.keys(this._gitStatus).some(p => p.startsWith(path + '/'));
      return hasChange ? 'ide-tree-git-changed' : '';
    }
    const status = this._gitStatus[path];
    if (!status) return '';
    const info = GIT_LABELS[status] || GIT_LABELS[status[0]] || null;
    return info ? info.cls : 'ide-git-modified';
  }

  _getGitDot(path, isDir) {
    const status = isDir ? null : this._gitStatus[path];
    if (!status) {
      if (isDir) {
        const hasChange = Object.keys(this._gitStatus).some(p => p.startsWith(path + '/'));
        if (hasChange) return '<span class="ide-git-dot modified"></span>';
      }
      return '';
    }
    if (status === 'M' || status[0] === 'M') return '<span class="ide-git-dot modified"></span>';
    if (status === 'A' || status[0] === 'A') return '<span class="ide-git-dot added"></span>';
    if (status === '?' || status === '??') return '<span class="ide-git-dot untracked"></span>';
    if (status === 'D' || status[0] === 'D') return '<span class="ide-git-dot deleted"></span>';
    return '<span class="ide-git-dot modified"></span>';
  }

  _bindTreeEvents(treeEl) {
    treeEl.addEventListener('click', async (e) => {
      const node = e.target.closest('.ide-tree-node');
      if (!node) return;

      const path = node.dataset.path;
      const type = node.dataset.type;

      if (type === 'directory') {
        if (this._expandedPaths.has(path)) {
          this._expandedPaths.delete(path);
        } else {
          this._expandedPaths.add(path);
          if (!this._treeCache.has(path)) {
            await this._loadDirectory(path);
          }
        }
        this._renderTree();
      } else {
        this._selectedFile = path;
        this._renderTree();
        await this._openFile(path);
      }
    });
  }

  // ── Tab management ─────────────────────────────────────────────────────────

  async _openFile(path) {
    // Check if already open
    const existing = this._openTabs.find(t => t.path === path);
    if (existing) {
      this._switchToTab(path);
      return;
    }

    this._logOutput(`Opening ${path}...`);

    try {
      const result = await api.readFile(this._project, path);
      if (result.binary) {
        this._logOutput(`Cannot edit binary file: ${path}`);
        return;
      }

      const ext = '.' + path.split('.').pop().toLowerCase();
      const lang = EXT_LANG[ext] || 'plaintext';

      const tab = {
        path,
        lang,
        content: result.content,
        originalContent: result.content,
        dirty: false,
        model: null,
        viewState: null,
      };

      this._openTabs.push(tab);
      this._switchToTab(path);
      this._logOutput(`Opened ${path} (${lang})`);
    } catch (e) {
      this._logOutput(`Error: ${e.message}`);
    }
  }

  _switchToTab(path) {
    // Save current view state
    if (this._activeTabPath && this._editor) {
      const current = this._openTabs.find(t => t.path === this._activeTabPath);
      if (current) {
        current.viewState = this._editor.saveViewState();
      }
    }

    this._activeTabPath = path;
    this._renderTabs();
    this._updateBreadcrumb();
    this._activateEditor();
  }

  _closeTab(path) {
    const idx = this._openTabs.findIndex(t => t.path === path);
    if (idx === -1) return;

    const tab = this._openTabs[idx];
    if (tab.model) tab.model.dispose();
    this._openTabs.splice(idx, 1);

    if (this._activeTabPath === path) {
      if (this._openTabs.length > 0) {
        const newIdx = Math.min(idx, this._openTabs.length - 1);
        this._switchToTab(this._openTabs[newIdx].path);
      } else {
        this._activeTabPath = null;
        this._renderTabs();
        this._updateBreadcrumb();
        this._showWelcome();
      }
    } else {
      this._renderTabs();
    }
    this._renderTree();
  }

  _renderTabs() {
    const bar = this._el.querySelector('.ide-tab-bar');
    if (!bar) return;

    if (this._openTabs.length === 0) {
      bar.innerHTML = '';
      return;
    }

    bar.innerHTML = this._openTabs.map(tab => {
      const isActive = tab.path === this._activeTabPath;
      const fileName = tab.path.split('/').pop();
      const ext = '.' + fileName.split('.').pop().toLowerCase();
      const lang = EXT_LANG[ext] || '';
      const accent = TAB_ACCENT[lang] || '#94a3b8';

      return `
        <div class="ide-tab${isActive ? ' active' : ''}${tab.dirty ? ' dirty' : ''}" data-path="${escapeHtml(tab.path)}" style="--tab-accent:${accent}">
          <span class="ide-tab-name">${escapeHtml(fileName)}</span>
          ${tab.dirty ? '<span class="ide-tab-dot"></span>' : ''}
          <button class="ide-tab-close" data-close="${escapeHtml(tab.path)}">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <path d="M2 2l6 6M8 2l-6 6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
      `;
    }).join('');

    // Bind tab events
    bar.querySelectorAll('.ide-tab').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.ide-tab-close')) return;
        this._switchToTab(el.dataset.path);
      });
    });
    bar.querySelectorAll('.ide-tab-close').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._closeTab(btn.dataset.close);
      });
    });
  }

  // ── Monaco Editor ──────────────────────────────────────────────────────────

  async _activateEditor() {
    const tab = this._openTabs.find(t => t.path === this._activeTabPath);
    if (!tab) return;

    const area = this._el.querySelector('.ide-editor-area');
    if (!area) return;

    // Ensure Monaco is loaded
    if (!_monacoLoaded) {
      area.innerHTML = '<div class="ide-editor-loading">Loading editor...</div>';
      try {
        await loadMonaco();
      } catch (e) {
        area.innerHTML = `<div class="ide-editor-loading">Failed to load Monaco: ${e.message}</div>`;
        return;
      }
    }

    // Create or reuse editor instance
    if (!this._editor) {
      area.innerHTML = '';
      const editorContainer = document.createElement('div');
      editorContainer.className = 'ide-monaco-container';
      area.appendChild(editorContainer);

      this._editor = monaco.editor.create(editorContainer, {
        theme: 'starship-void',
        fontFamily: "'IBM Plex Mono', 'Fira Code', monospace",
        fontSize: 13,
        lineHeight: 20,
        minimap: { enabled: true, side: 'right', size: 'proportional', maxColumn: 80 },
        scrollBeyondLastLine: true,
        smoothScrolling: true,
        cursorBlinking: 'smooth',
        cursorSmoothCaretAnimation: 'on',
        renderLineHighlight: 'all',
        bracketPairColorization: { enabled: true },
        guides: { bracketPairs: true, indentation: true },
        padding: { top: 8, bottom: 8 },
        automaticLayout: true,
        wordWrap: 'off',
        tabSize: 2,
        insertSpaces: true,
        formatOnPaste: false,
        formatOnType: false,
        renderWhitespace: 'selection',
        overviewRulerBorder: false,
        scrollbar: {
          verticalScrollbarSize: 10,
          horizontalScrollbarSize: 10,
          useShadows: false,
        },
      });

      this._editorReady = true;
    }

    // Create or reuse model for this tab
    if (!tab.model) {
      const uri = monaco.Uri.parse(`file:///${this._project}/${tab.path}`);
      tab.model = monaco.editor.createModel(tab.content, tab.lang, uri);

      // Track changes for dirty state
      tab.model.onDidChangeContent(() => {
        const newContent = tab.model.getValue();
        const wasDirty = tab.dirty;
        tab.dirty = newContent !== tab.originalContent;
        if (wasDirty !== tab.dirty) {
          this._renderTabs();
        }
      });
    }

    this._editor.setModel(tab.model);

    // Restore view state if available
    if (tab.viewState) {
      this._editor.restoreViewState(tab.viewState);
    }

    this._editor.focus();
  }

  _showWelcome() {
    const area = this._el.querySelector('.ide-editor-area');
    if (!area) return;

    if (this._editor) {
      this._editor.dispose();
      this._editor = null;
      this._editorReady = false;
    }

    area.innerHTML = `
      <div class="ide-editor-welcome">
        <div class="ide-welcome-icon">
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none" opacity="0.4">
            <path d="M15 12l-9 12 9 12M33 12l9 12-9 12M28 8L20 40" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <span class="ide-welcome-title">Stellar Code Editor</span>
        <span class="ide-welcome-hint">Open a file from the explorer to start editing</span>
        <div class="ide-welcome-shortcuts">
          <span><kbd>Cmd+S</kbd> Save file</span>
          <span><kbd>Cmd+P</kbd> Quick open</span>
          <span><kbd>Cmd+W</kbd> Close tab</span>
        </div>
      </div>
    `;
  }

  // ── Save ───────────────────────────────────────────────────────────────────

  async _saveActiveFile() {
    const tab = this._openTabs.find(t => t.path === this._activeTabPath);
    if (!tab || !tab.dirty) return;

    const content = tab.model ? tab.model.getValue() : tab.content;

    try {
      await api.writeFile(this._project, tab.path, content);
      tab.originalContent = content;
      tab.dirty = false;
      this._renderTabs();
      this._logOutput(`Saved ${tab.path}`);
      toast(`Saved ${tab.path.split('/').pop()}`);

      // Refresh git status
      try {
        this._gitStatus = await api.getGitStatus(this._project);
        this._renderTree();
      } catch (_) {}
    } catch (e) {
      this._logOutput(`Error saving: ${e.message}`);
      toast(`Save failed: ${e.message}`, 'error');
    }
  }

  // ── Breadcrumb ─────────────────────────────────────────────────────────────

  _updateBreadcrumb() {
    const bc = this._el.querySelector('.ide-breadcrumb');
    if (!bc) return;

    if (!this._activeTabPath) {
      bc.innerHTML = `<span class="ide-breadcrumb-segment">${escapeHtml(this._project)}</span>`;
      return;
    }

    const parts = this._activeTabPath.split('/');
    bc.innerHTML = `<span class="ide-breadcrumb-segment clickable" data-bc-path="">${escapeHtml(this._project)}</span>` +
      parts.map((p, i) => {
        const subpath = parts.slice(0, i + 1).join('/');
        const isLast = i === parts.length - 1;
        return `<span class="ide-breadcrumb-sep">/</span><span class="ide-breadcrumb-segment${isLast ? ' current' : ' clickable'}" data-bc-path="${escapeHtml(subpath)}">${escapeHtml(p)}</span>`;
      }).join('');
  }

  _updateBranchIndicator() {
    const el = this._el.querySelector('.ide-branch-name');
    if (el) el.textContent = this._gitBranch || 'detached';
  }

  // ── Bottom panel ───────────────────────────────────────────────────────────

  _toggleBottom() {
    this._bottomVisible = !this._bottomVisible;
    const panel = this._el.querySelector('.ide-bottom-panel');
    if (panel) {
      panel.classList.toggle('collapsed', !this._bottomVisible);
    }
    // Trigger editor relayout
    if (this._editor) {
      setTimeout(() => this._editor.layout(), 200);
    }
  }

  _logOutput(msg) {
    const now = new Date();
    const ts = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    this._bottomOutput.push({ ts, msg });
    if (this._bottomOutput.length > 500) this._bottomOutput.shift();
    this._renderBottomContent();
  }

  _renderBottomContent() {
    const content = this._el.querySelector('.ide-bottom-content');
    if (!content) return;
    content.innerHTML = this._bottomOutput.map(({ ts, msg }) =>
      `<div class="ide-output-line"><span class="ide-output-ts">${ts}</span> ${escapeHtml(msg)}</div>`
    ).join('');
    content.scrollTop = content.scrollHeight;
  }

  // ── Resize handlers ────────────────────────────────────────────────────────

  _startSidebarResize(e) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = this._sidebarWidth;

    const onMove = (e) => {
      const newWidth = Math.max(160, Math.min(500, startWidth + (e.clientX - startX)));
      this._sidebarWidth = newWidth;
      const sidebar = this._el.querySelector('.ide-sidebar');
      if (sidebar) sidebar.style.width = `${newWidth}px`;
    };

    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (this._editor) this._editor.layout();
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  _startBottomResize(e) {
    e.preventDefault();
    const startY = e.clientY;
    const startHeight = this._bottomHeight;

    const onMove = (e) => {
      const newHeight = Math.max(80, Math.min(400, startHeight - (e.clientY - startY)));
      this._bottomHeight = newHeight;
      const panel = this._el.querySelector('.ide-bottom-panel');
      if (panel) panel.style.height = `${newHeight}px`;
    };

    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (this._editor) this._editor.layout();
    };

    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }
}
