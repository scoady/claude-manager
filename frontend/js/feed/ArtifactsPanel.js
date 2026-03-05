/** ArtifactsPanel — split-pane file tree + content preview. */
import { escapeHtml } from '../utils.js';
import { api } from '../api.js';

const FILE_ICONS = {
  directory: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M2 3h4l2 2h6v8H2V3z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  file: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M4 2h5l4 4v8H4V2z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9 2v4h4" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
};

const GIT_LABELS = {
  'M': { label: 'M', cls: 'git-modified' },
  'A': { label: '+', cls: 'git-added' },
  'D': { label: '-', cls: 'git-deleted' },
  '?': { label: '?', cls: 'git-untracked' },
  '??': { label: '?', cls: 'git-untracked' },
  'R': { label: 'R', cls: 'git-renamed' },
};

// Language detection from file extension
const EXT_LANG = {
  '.js': 'javascript', '.mjs': 'javascript', '.jsx': 'javascript',
  '.ts': 'typescript', '.tsx': 'typescript',
  '.py': 'python', '.pyw': 'python',
  '.rb': 'ruby', '.rs': 'rust', '.go': 'go',
  '.java': 'java', '.kt': 'kotlin',
  '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.cc': 'cpp', '.hpp': 'cpp',
  '.cs': 'csharp', '.swift': 'swift',
  '.html': 'xml', '.htm': 'xml', '.xml': 'xml', '.svg': 'xml',
  '.css': 'css', '.scss': 'scss', '.less': 'less',
  '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'ini',
  '.md': 'markdown', '.mdx': 'markdown',
  '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
  '.sql': 'sql', '.graphql': 'graphql',
  '.dockerfile': 'dockerfile', '.tf': 'hcl',
  '.r': 'r', '.lua': 'lua', '.php': 'php',
};

export class ArtifactsPanel {
  constructor(projectName) {
    this._project = projectName;
    this._gitStatus = {};
    this._expandedPaths = new Set();
    this._treeCache = new Map(); // path -> children
    this._selectedFile = null;
    this._el = document.createElement('div');
    this._el.className = 'artifacts-panel';
    this._render();
  }

  get el() { return this._el; }

  async load() {
    try {
      this._gitStatus = await api.getGitStatus(this._project);
    } catch (_) {
      this._gitStatus = {};
    }
    await this._loadDirectory('');
    this._renderTree();
  }

  destroy() {}

  async _loadDirectory(path) {
    try {
      const files = await api.listFiles(this._project, path);
      this._treeCache.set(path, files);
      return files;
    } catch (e) {
      console.error('Failed to load directory:', e);
      return [];
    }
  }

  _render() {
    this._el.innerHTML = `
      <div class="artifacts-split">
        <div class="artifacts-tree-pane">
          <div class="artifacts-tree-header">Files</div>
          <div class="artifacts-tree" role="tree"></div>
        </div>
        <div class="artifacts-preview-pane">
          <div class="artifacts-breadcrumb"></div>
          <div class="artifacts-preview-content">
            <div class="artifacts-empty-preview">
              <svg width="32" height="32" viewBox="0 0 32 32" fill="none" opacity="0.3">
                <path d="M8 4h10l8 8v16H8V4z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
                <path d="M18 4v8h8" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
              </svg>
              <span>Select a file to preview</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  _renderTree() {
    const treeEl = this._el.querySelector('.artifacts-tree');
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
      const gitInfo = this._getGitBadge(entry.path, isDir);
      const icon = isDir
        ? `<span class="tree-chevron ${isExpanded ? 'open' : ''}">
            <svg width="8" height="8" viewBox="0 0 8 8"><path d="M2 1l4 3-4 3" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </span>`
        : '<span class="tree-chevron-spacer"></span>';

      let children = '';
      if (isDir && isExpanded) {
        const childEntries = this._treeCache.get(entry.path) || [];
        children = `<div class="tree-children-group">${this._buildTreeHtml(childEntries, depth + 1)}</div>`;
      }

      return `
        <div class="tree-node${isSelected ? ' selected' : ''}" data-path="${escapeHtml(entry.path)}" data-type="${entry.type}" style="--depth: ${depth}">
          ${icon}
          <span class="tree-icon">${FILE_ICONS[entry.type]}</span>
          <span class="tree-name">${escapeHtml(entry.name)}</span>
          ${gitInfo}
        </div>
        ${children}
      `;
    }).join('');
  }

  _getGitBadge(path, isDir) {
    if (isDir) {
      // Check if any child has git status
      const hasChange = Object.keys(this._gitStatus).some(p => p.startsWith(path + '/'));
      if (hasChange) return '<span class="git-badge git-modified">*</span>';
      return '';
    }
    const status = this._gitStatus[path];
    if (!status) return '';
    const info = GIT_LABELS[status] || GIT_LABELS[status[0]] || null;
    if (!info) return `<span class="git-badge git-modified">${escapeHtml(status)}</span>`;
    return `<span class="git-badge ${info.cls}">${info.label}</span>`;
  }

  _bindTreeEvents(treeEl) {
    treeEl.addEventListener('click', async (e) => {
      const node = e.target.closest('.tree-node');
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
        await this._loadFilePreview(path);
      }
    });
  }

  async _loadFilePreview(path) {
    const preview = this._el.querySelector('.artifacts-preview-content');
    const breadcrumb = this._el.querySelector('.artifacts-breadcrumb');
    if (!preview) return;

    // Update breadcrumb
    const parts = path.split('/');
    breadcrumb.innerHTML = parts.map((p, i) => {
      const isLast = i === parts.length - 1;
      return `<span class="breadcrumb-segment${isLast ? ' current' : ''}">${escapeHtml(p)}</span>`;
    }).join('<span class="breadcrumb-sep">/</span>');

    preview.innerHTML = '<div class="artifacts-loading">Loading...</div>';

    try {
      const result = await api.readFile(this._project, path);

      if (result.binary) {
        preview.innerHTML = `
          <div class="artifacts-empty-preview">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" opacity="0.3">
              <rect x="4" y="4" width="24" height="24" rx="4" stroke="currentColor" stroke-width="1.5"/>
              <path d="M10 22l5-6 4 3 4-5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <span>Binary file (${this._formatSize(result.size)})</span>
          </div>`;
        return;
      }

      const ext = '.' + path.split('.').pop().toLowerCase();
      const lang = EXT_LANG[ext] || '';
      const lines = result.content.split('\n');

      // Line numbers + content
      const lineNums = lines.map((_, i) => `<span>${i + 1}</span>`).join('\n');
      const codeContent = escapeHtml(result.content);

      preview.innerHTML = `
        ${result.truncated ? '<div class="artifacts-truncated-warning">File truncated (showing first 500KB)</div>' : ''}
        <div class="artifacts-code-wrap">
          <div class="artifacts-line-numbers">${lineNums}</div>
          <pre class="artifacts-code"><code class="${lang ? `language-${lang}` : ''}">${codeContent}</code></pre>
        </div>
      `;

      // Try highlight.js if available
      if (lang && typeof hljs !== 'undefined') {
        const codeEl = preview.querySelector('code');
        if (codeEl) {
          try { hljs.highlightElement(codeEl); } catch (_) {}
        }
      }
    } catch (e) {
      preview.innerHTML = `<div class="artifacts-empty-preview"><span>Failed to load file: ${escapeHtml(e.message)}</span></div>`;
    }
  }

  _formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
}
