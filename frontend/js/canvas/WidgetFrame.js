/**
 * WidgetFrame — lightweight wrapper for a single canvas widget.
 *
 * No Shadow DOM — widgets inherit the host page's full design system
 * (CSS variables, fonts, classes). Widget-specific CSS is scoped via
 * a `data-widget-id` attribute selector injected into a <style> tag.
 * The outer `.widget-frame` element is what CanvasEngine places inside
 * the CSS Grid; it contains the header bar and the user-supplied content area.
 */

export class WidgetFrame {
  /**
   * @param {object} widgetDef
   * @param {string}  widgetDef.widget_id
   * @param {string}  [widgetDef.title]
   * @param {string}  [widgetDef.agent]       — attribution label
   * @param {string}  [widgetDef.html]
   * @param {string}  [widgetDef.css]
   * @param {string}  [widgetDef.js]
   * @param {number}  [widgetDef.grid_col]
   * @param {number}  [widgetDef.grid_row]
   * @param {number}  [widgetDef.col_span]
   * @param {number}  [widgetDef.row_span]
   * @param {function} [widgetDef.onRemove]   — called when × is clicked
   */
  constructor(widgetDef) {
    this._def = widgetDef;
    this._hostEl = null;
    this._contentEl = null;
    this._titleEl = null;
    this._agentEl = null;
    this._customStyleEl = null;

    this._build();
    this._setupContextMenu();
  }

  /** The outer host element that goes into the grid. */
  get element() {
    return this._hostEl;
  }

  // ── Build ────────────────────────────────────────────────────────────────────

  _build() {
    // Outer host div — lives in the CSS Grid.
    this._hostEl = document.createElement('div');
    this._hostEl.className = 'widget-frame entering';
    this._hostEl.dataset.widgetId = this._def.widget_id;

    // Grid placement is handled by GridStack — no inline styles needed.

    // Widget-specific CSS (if any) — scoped via data attribute.
    if (this._def.css) {
      this._customStyleEl = document.createElement('style');
      this._customStyleEl.dataset.role = 'widget-css';
      this._customStyleEl.dataset.widgetOwner = this._def.widget_id;
      this._customStyleEl.textContent = this._scopeCSS(this._def.css);
      document.head.appendChild(this._customStyleEl);
    }

    // Header bar.
    const header = document.createElement('div');
    header.className = 'widget-header';

    this._titleEl = document.createElement('span');
    this._titleEl.className = 'widget-title';
    this._titleEl.textContent = this._def.title || this._def.widget_id;
    header.appendChild(this._titleEl);

    if (this._def.agent) {
      this._agentEl = document.createElement('span');
      this._agentEl.className = 'widget-agent';
      this._agentEl.textContent = this._def.agent;
      header.appendChild(this._agentEl);
    }

    const removeBtn = document.createElement('button');
    removeBtn.className = 'widget-remove';
    removeBtn.title = 'Remove widget';
    removeBtn.innerHTML = '&times;';
    // Stop propagation to prevent GridStack from intercepting the click
    removeBtn.addEventListener('mousedown', (e) => e.stopPropagation());
    removeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (typeof this._def.onRemove === 'function') {
        this._def.onRemove(this._def.widget_id);
      }
    });
    header.appendChild(removeBtn);

    this._hostEl.appendChild(header);

    // Content area.
    this._contentEl = document.createElement('div');
    this._contentEl.className = 'widget-content';
    this._hostEl.appendChild(this._contentEl);

    // Inject initial HTML + JS.
    this._injectContent(this._def);
  }

  // ── CSS scoping ──────────────────────────────────────────────────────────────

  /**
   * Scope widget CSS by prepending each rule with a data-attribute selector.
   * This prevents agent-provided CSS from leaking into the host page.
   */
  _scopeCSS(css) {
    const scope = `[data-widget-id="${this._def.widget_id}"]`;
    // Handle @keyframes — leave them unscoped (they're name-isolated)
    return css.replace(
      /([^@{}]+)\{/g,
      (match, selector) => {
        // Don't scope @-rules or closing braces
        if (selector.trim().startsWith('@') || selector.trim() === '') return match;
        // Scope each comma-separated selector
        const scoped = selector
          .split(',')
          .map(s => `${scope} ${s.trim()}`)
          .join(', ');
        return `${scoped} {`;
      }
    );
  }

  // ── Content injection ────────────────────────────────────────────────────────

  _injectContent(def) {
    if (def.html !== undefined) {
      this._contentEl.innerHTML = def.html || '';
      // Security: strip any <script> tags from agent-provided HTML.
      this._contentEl.querySelectorAll('script').forEach(s => s.remove());
    }
    if (def.js) {
      this._execJS(def.js);
    }
  }

  _execJS(code) {
    try {
      // Run in the context of the content element.
      // eslint-disable-next-line no-new-func
      const fn = new Function('root', 'host', code);
      fn(this._contentEl, this._hostEl);
    } catch (err) {
      this._renderError(err);
    }
  }

  _renderError(err) {
    const errorEl = document.createElement('div');
    errorEl.className = 'widget-error';
    errorEl.innerHTML = `
      <span class="widget-error-title">Widget script error</span>
      <pre class="widget-error-msg">${this._escapeHtml(String(err))}</pre>
    `;
    this._contentEl.prepend(errorEl);
    this._hostEl.style.setProperty('border-color', 'rgba(248,113,113,0.5)');
  }

  _escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Grid placement ───────────────────────────────────────────────────────────

  _applyGridPlacement(def) {
    const colSpan = def.col_span  ?? 1;
    const rowSpan = def.row_span  ?? 1;

    this._hostEl.style.gridColumn = `auto / span ${colSpan}`;
    this._hostEl.style.gridRow = `auto / span ${rowSpan}`;
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  /**
   * Apply a partial update to the widget.
   * Only fields present in `patch` are touched.
   * @param {Partial<typeof this._def>} patch
   */
  update(patch) {
    Object.assign(this._def, patch);

    if (patch.title !== undefined) {
      this._titleEl.textContent = patch.title || this._def.widget_id;
    }

    if (patch.agent !== undefined) {
      if (this._agentEl) {
        this._agentEl.textContent = patch.agent;
      } else if (patch.agent) {
        this._agentEl = document.createElement('span');
        this._agentEl.className = 'widget-agent';
        this._agentEl.textContent = patch.agent;
        const header = this._hostEl.querySelector('.widget-header');
        header.insertBefore(this._agentEl, header.lastElementChild);
      }
    }

    if (patch.css !== undefined) {
      if (!this._customStyleEl) {
        this._customStyleEl = document.createElement('style');
        this._customStyleEl.dataset.role = 'widget-css';
        this._customStyleEl.dataset.widgetOwner = this._def.widget_id;
        document.head.appendChild(this._customStyleEl);
      }
      this._customStyleEl.textContent = this._scopeCSS(patch.css);
    }

    if (patch.html !== undefined || patch.js !== undefined) {
      if (patch.html !== undefined) {
        this._contentEl.innerHTML = patch.html || '';
        this._contentEl.querySelectorAll('script').forEach(s => s.remove());
      }
      if (patch.js !== undefined) {
        this._execJS(patch.js);
      }
    }

    if (
      patch.grid_col !== undefined ||
      patch.grid_row !== undefined ||
      patch.col_span !== undefined ||
      patch.row_span !== undefined
    ) {
      this._applyGridPlacement(this._def);
    }
  }

  /**
   * Trigger a CSS keyframe animation on the host element.
   * @param {string} keyframesCss  — complete `@keyframes` block
   * @param {object} [opts]
   */
  animate(keyframesCss, opts = {}) {
    const name = `wf-anim-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    const finalCss = keyframesCss.replace(/@keyframes\s+\S+/, `@keyframes ${name}`);
    const styleEl = document.createElement('style');
    styleEl.textContent = finalCss;
    document.head.appendChild(styleEl);

    const duration = opts.duration || '600ms';
    const easing   = opts.easing   || 'cubic-bezier(0.22, 1, 0.36, 1)';
    const fill     = opts.fill     || 'both';

    this._hostEl.style.animation = `${name} ${duration} ${easing} ${fill}`;

    this._hostEl.addEventListener('animationend', () => {
      this._hostEl.style.animation = '';
      styleEl.remove();
    }, { once: true });
  }

  /** Trigger the removal exit animation then call `onDone`. */
  remove(onDone) {
    this._hostEl.classList.add('removing');
    this._hostEl.addEventListener('transitionend', () => {
      onDone?.();
    }, { once: true });
    setTimeout(() => onDone?.(), 400);
  }

  /** Trigger the entrance animation (called by CanvasEngine after DOM insertion). */
  triggerEntrance() {
    void this._hostEl.offsetWidth;
    this._hostEl.classList.remove('entering');
    this._hostEl.classList.add('visible');
  }

  // ── Context Menu (right-click) ─────────────────────────────────────────────

  _setupContextMenu() {
    this._hostEl.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this._showContextMenu(e.clientX, e.clientY);
    });
  }

  _showContextMenu(x, y) {
    // Remove any existing menu
    document.querySelector('.wf-ctx-menu')?.remove();

    const menu = document.createElement('div');
    menu.className = 'wf-ctx-menu';
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    const items = [
      { label: 'Copy Widget', icon: '\u2398', action: () => this._copyWidget() },
      { label: 'Save as Template', icon: '\u2605', action: () => this._saveAsTemplate() },
      { label: 'Paste from Template\u2026', icon: '\u2913', action: () => this._pasteFromTemplate() },
      null, // separator
      { label: 'Copy JSON', icon: '{}', action: () => this._copyJSON() },
    ];

    items.forEach(item => {
      if (!item) {
        const sep = document.createElement('div');
        sep.className = 'wf-ctx-sep';
        menu.appendChild(sep);
        return;
      }
      const el = document.createElement('div');
      el.className = 'wf-ctx-item';
      el.innerHTML = `<span class="wf-ctx-icon">${item.icon}</span>${item.label}`;
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        menu.remove();
        item.action();
      });
      menu.appendChild(el);
    });

    document.body.appendChild(menu);

    // Keep menu in viewport
    requestAnimationFrame(() => {
      const rect = menu.getBoundingClientRect();
      if (rect.right > window.innerWidth) menu.style.left = `${x - rect.width}px`;
      if (rect.bottom > window.innerHeight) menu.style.top = `${y - rect.height}px`;
    });

    // Close on click outside
    const close = () => { menu.remove(); document.removeEventListener('click', close); };
    setTimeout(() => document.addEventListener('click', close), 0);
  }

  _getWidgetData() {
    return {
      id: this._def.widget_id,
      title: this._def.title || '',
      html: this._def.html || '',
      css: this._def.css || '',
      js: this._def.js || '',
      gs_w: this._def.gs_w,
      gs_h: this._def.gs_h,
      gs_x: this._def.gs_x,
      gs_y: this._def.gs_y,
      no_resize: this._def.no_resize,
      no_move: this._def.no_move,
    };
  }

  async _copyWidget() {
    // Store in a global clipboard for paste across widgets
    window.__widgetClipboard = this._getWidgetData();
    this._toast('Widget copied');
  }

  async _copyJSON() {
    const data = this._getWidgetData();
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      this._toast('JSON copied to clipboard');
    } catch {
      window.__widgetClipboard = data;
      this._toast('Widget copied (clipboard unavailable)');
    }
  }

  async _saveAsTemplate() {
    const data = this._getWidgetData();
    // Get the project from the nearest canvas context
    const project = this._hostEl.closest('[data-project]')?.dataset.project
      || document.querySelector('[data-active-project]')?.dataset.activeProject
      || '';
    data._source_project = project;
    try {
      const resp = await fetch('/api/canvas/templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (resp.ok) {
        this._toast(`Saved template: ${data.id}`);
      } else {
        this._toast('Failed to save template', true);
      }
    } catch {
      this._toast('Failed to save template', true);
    }
  }

  async _pasteFromTemplate() {
    try {
      const resp = await fetch('/api/canvas/templates');
      if (!resp.ok) return;
      const templates = await resp.json();
      if (!templates.length) {
        this._toast('No templates saved');
        return;
      }
      this._showTemplatePicker(templates);
    } catch {
      this._toast('Failed to load templates', true);
    }
  }

  _showTemplatePicker(templates) {
    document.querySelector('.wf-tpl-picker')?.remove();

    const overlay = document.createElement('div');
    overlay.className = 'wf-tpl-picker';

    const panel = document.createElement('div');
    panel.className = 'wf-tpl-panel';
    panel.innerHTML = '<div class="wf-tpl-title">Paste from Template</div>';

    const list = document.createElement('div');
    list.className = 'wf-tpl-list';

    templates.forEach(t => {
      const item = document.createElement('div');
      item.className = 'wf-tpl-item';
      item.innerHTML = `<span class="wf-tpl-name">${this._escapeHtml(t.title || t.id)}</span>
        <span class="wf-tpl-size">${t.gs_w || '?'}\u00d7${t.gs_h || '?'}</span>`;
      item.addEventListener('click', () => {
        overlay.remove();
        this._applyTemplate(t.filename);
      });
      list.appendChild(item);
    });

    panel.appendChild(list);
    overlay.appendChild(panel);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }

  async _applyTemplate(filename) {
    const project = this._hostEl.closest('[data-project]')?.dataset.project
      || document.querySelector('[data-active-project]')?.dataset.activeProject
      || '';
    try {
      const resp = await fetch(
        `/api/canvas/${encodeURIComponent(project)}/widgets/${encodeURIComponent(this._def.widget_id)}/paste-template`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ template: filename }),
        }
      );
      if (resp.ok) {
        this._toast('Template applied');
      } else {
        this._toast('Failed to apply template', true);
      }
    } catch {
      this._toast('Failed to apply template', true);
    }
  }

  _toast(msg, isError = false) {
    const el = document.createElement('div');
    el.className = 'wf-toast' + (isError ? ' wf-toast-err' : '');
    el.textContent = msg;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add('wf-toast-show'));
    setTimeout(() => { el.classList.remove('wf-toast-show'); setTimeout(() => el.remove(), 300); }, 2000);
  }

  /**
   * Clean up any resources held by this frame.
   */
  destroy() {
    // Remove scoped style tag from document head.
    if (this._customStyleEl) {
      this._customStyleEl.remove();
      this._customStyleEl = null;
    }
  }
}
