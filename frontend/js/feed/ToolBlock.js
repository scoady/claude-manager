/** ToolBlock — renders a single tool call/result pair as a collapsible block. */
import { escapeHtml } from '../utils.js';

const TOOL_ICONS = {
  Read:    '<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M2 1.5h7a.5.5 0 01.5.5v7a.5.5 0 01-.5.5H2a.5.5 0 01-.5-.5V2a.5.5 0 01.5-.5z" stroke="currentColor" stroke-width="1.2"/><path d="M3.5 4h4M3.5 5.5h4M3.5 7h2.5" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/></svg>',
  Write:   '<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M7.5 1.5l2 2-5 5H2.5v-2l5-5z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>',
  Edit:    '<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M7.5 1.5l2 2-5 5H2.5v-2l5-5z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>',
  Bash:    '<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M2 3.5l2.5 2-2.5 2M5.5 7.5h3.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  Glob:    '<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><circle cx="5.5" cy="5.5" r="3.5" stroke="currentColor" stroke-width="1.2"/><path d="M2 5.5h7M5.5 2c-1 1.5-1 4 0 7M5.5 2c1 1.5 1 4 0 7" stroke="currentColor" stroke-width="1"/></svg>',
  Grep:    '<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><circle cx="4.5" cy="4.5" r="3" stroke="currentColor" stroke-width="1.2"/><path d="M7 7l2.5 2.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
  WebFetch:'<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><circle cx="5.5" cy="5.5" r="3.5" stroke="currentColor" stroke-width="1.2"/><path d="M9 2L2 9" stroke="currentColor" stroke-width="1" stroke-linecap="round" opacity="0.5"/></svg>',
  default: '<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M5.5 1v9M1 5.5h9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
};

let _blockCounter = 0;

export class ToolBlock {
  /**
   * @param {object} opts
   * @param {string} opts.toolId    — unique id from the backend
   * @param {string} opts.toolName
   * @param {any}    opts.toolInput
   */
  constructor({ toolId, toolName, toolInput = {} }) {
    this.toolId    = toolId;
    this.toolName  = toolName;
    this.toolInput = toolInput;
    this._domId    = `tb-${++_blockCounter}`;
    this._expanded = false;
    this._startMs  = Date.now();
    this._duration = null;
    this.el        = this._build();
  }

  _build() {
    const el = document.createElement('div');
    el.className = 'tool-block';
    el.id = this._domId;
    el.innerHTML = this._headerHTML() + `<div class="tool-block-body"></div>`;
    el.querySelector('.tool-block-header').addEventListener('click', () => this.toggle());
    return el;
  }

  _headerHTML() {
    const icon = TOOL_ICONS[this.toolName] || TOOL_ICONS.default;
    const inputPreview = this._inputPreview();
    const dur = this._duration != null ? `<span class="tool-block-dur">${this._duration}ms</span>` : '';
    return `
      <div class="tool-block-header">
        <span class="tool-block-icon">${icon}</span>
        <span class="tool-block-name">${escapeHtml(this.toolName)}</span>
        <span class="tool-block-preview">${escapeHtml(inputPreview)}</span>
        ${dur}
        <svg class="tool-block-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M3 4l2 2 2-2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>`;
  }

  _inputPreview() {
    // Show first meaningful string value from input
    if (!this.toolInput || typeof this.toolInput !== 'object') return '';
    const vals = Object.values(this.toolInput);
    for (const v of vals) {
      if (typeof v === 'string' && v.trim()) {
        return v.length > 80 ? v.slice(0, 80) + '…' : v;
      }
    }
    return '';
  }

  _bodyHTML(output) {
    const inputRows = Object.entries(this.toolInput || {})
      .slice(0, 8)
      .map(([k, v]) => {
        const val = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
        return `<div class="tool-body-row"><span class="tool-body-key">${escapeHtml(k)}</span><span class="tool-body-val">${escapeHtml(val.length > 300 ? val.slice(0, 300) + '…' : val)}</span></div>`;
      }).join('');

    const outputHtml = output
      ? `<div class="tool-body-output">${escapeHtml(typeof output === 'string' ? output.slice(0, 1000) : JSON.stringify(output, null, 2).slice(0, 1000))}${(output?.length || 0) > 1000 ? '\n…' : ''}</div>`
      : '';

    return `${inputRows}${outputHtml}`;
  }

  /** Called when tool result arrives. */
  setOutput(output) {
    this._duration = Date.now() - this._startMs;
    this.el.classList.add('has-output');
    // Refresh header (adds duration)
    const header = this.el.querySelector('.tool-block-header');
    if (header) {
      header.outerHTML = this._headerHTML();
      this.el.querySelector('.tool-block-header').addEventListener('click', () => this.toggle());
    }
    if (this._expanded) {
      const body = this.el.querySelector('.tool-block-body');
      if (body) body.innerHTML = this._bodyHTML(output);
    }
    this._output = output;
  }

  toggle() {
    this._expanded = !this._expanded;
    this.el.classList.toggle('expanded', this._expanded);
    const body = this.el.querySelector('.tool-block-body');
    if (body) body.innerHTML = this._expanded ? this._bodyHTML(this._output) : '';
  }
}
