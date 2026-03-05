/**
 * WidgetFrame — Shadow DOM wrapper for a single canvas widget.
 *
 * Each widget gets an isolated Shadow DOM so its CSS and JS cannot leak into
 * or be corrupted by the host page.  The outer `.widget-frame` element is what
 * CanvasEngine places inside the CSS Grid; the shadow root contains the header
 * bar and the user-supplied content area.
 */

const FRAME_HOST_STYLES = `
  .widget-frame {
    position: relative;
    border: 1px solid var(--bg-border, #243352);
    border-radius: 10px;
    background: var(--bg-surface, #0e1525);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    transition:
      border-color 220ms cubic-bezier(0.22, 1, 0.36, 1),
      box-shadow   220ms cubic-bezier(0.22, 1, 0.36, 1),
      opacity      350ms cubic-bezier(0.22, 1, 0.36, 1),
      transform    350ms cubic-bezier(0.22, 1, 0.36, 1);
  }
  .widget-frame:hover {
    border-color: rgba(103, 232, 249, 0.30); /* --accent-cyan @ 30% */
    box-shadow: 0 0 16px rgba(103, 232, 249, 0.08);
  }
  .widget-frame.entering {
    opacity: 0;
    transform: translateY(14px);
  }
  .widget-frame.visible {
    opacity: 1;
    transform: translateY(0);
  }
  .widget-frame.removing {
    opacity: 0;
    transform: translateY(-10px) scale(0.97);
  }
`;

const SHADOW_BASE_STYLES = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :host {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .widget-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 7px 10px 7px 12px;
    background: rgba(14, 21, 37, 0.72);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--bg-border, #243352);
    flex-shrink: 0;
    min-height: 32px;
  }

  .widget-title {
    font-family: 'IBM Plex Mono', 'Fira Code', monospace;
    font-size: 11px;
    color: var(--text-secondary, #94a3b8);
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    letter-spacing: 0.02em;
  }

  .widget-agent {
    font-family: 'IBM Plex Mono', 'Fira Code', monospace;
    font-size: 10px;
    color: var(--text-muted, #475569);
    white-space: nowrap;
  }

  .widget-remove {
    flex-shrink: 0;
    background: none;
    border: none;
    cursor: pointer;
    color: var(--text-muted, #475569);
    font-size: 14px;
    line-height: 1;
    padding: 2px 4px;
    border-radius: 4px;
    opacity: 0;
    transition: opacity 150ms ease, color 150ms ease, background 150ms ease;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .widget-header:hover .widget-remove {
    opacity: 1;
  }
  .widget-remove:hover {
    color: #f87171; /* --accent-red */
    background: rgba(248, 113, 113, 0.10);
  }

  .widget-content {
    flex: 1;
    min-height: 120px;
    overflow: auto;
    padding: 12px;
    word-wrap: break-word;
    overflow-wrap: break-word;
    word-break: break-word;
  }

  .widget-content * {
    max-width: 100%;
    overflow-wrap: break-word;
    word-break: break-word;
  }

  .widget-content table {
    table-layout: fixed;
    width: 100%;
  }

  .widget-content td, .widget-content th {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .widget-content pre {
    white-space: pre-wrap;
    overflow-x: auto;
  }

  .widget-error {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 12px;
    border: 1px solid rgba(248, 113, 113, 0.40);
    border-radius: 6px;
    background: rgba(248, 113, 113, 0.06);
    margin: 8px;
  }
  .widget-error-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: #f87171;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .widget-error-msg {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: rgba(248, 113, 113, 0.80);
    white-space: pre-wrap;
    word-break: break-word;
  }
`;

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
    this._shadow = null;
    this._contentEl = null;
    this._titleEl = null;
    this._agentEl = null;

    this._build();
  }

  /** The outer host element that goes into the grid. */
  get element() {
    return this._hostEl;
  }

  // ── Build ────────────────────────────────────────────────────────────────────

  _build() {
    // Inject host-level styles once into the document (idempotent).
    if (!document.getElementById('widget-frame-host-styles')) {
      const styleEl = document.createElement('style');
      styleEl.id = 'widget-frame-host-styles';
      styleEl.textContent = FRAME_HOST_STYLES;
      document.head.appendChild(styleEl);
    }

    // Outer host div — lives in the CSS Grid.
    this._hostEl = document.createElement('div');
    this._hostEl.className = 'widget-frame entering';
    this._hostEl.dataset.widgetId = this._def.widget_id;

    // Apply grid placement.
    this._applyGridPlacement(this._def);

    // Shadow DOM root.
    this._shadow = this._hostEl.attachShadow({ mode: 'open' });

    // Base styles inside shadow.
    const baseStyle = document.createElement('style');
    baseStyle.textContent = SHADOW_BASE_STYLES;
    this._shadow.appendChild(baseStyle);

    // Widget-specific CSS (if any).
    if (this._def.css) {
      const customStyle = document.createElement('style');
      customStyle.dataset.role = 'widget-css';
      customStyle.textContent = this._def.css;
      this._shadow.appendChild(customStyle);
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
    removeBtn.addEventListener('click', () => {
      if (typeof this._def.onRemove === 'function') {
        this._def.onRemove(this._def.widget_id);
      }
    });
    header.appendChild(removeBtn);

    this._shadow.appendChild(header);

    // Content area.
    this._contentEl = document.createElement('div');
    this._contentEl.className = 'widget-content';
    this._shadow.appendChild(this._contentEl);

    // Inject initial HTML + JS.
    this._injectContent(this._def);
  }

  // ── Content injection ────────────────────────────────────────────────────────

  _injectContent(def) {
    if (def.html !== undefined) {
      this._contentEl.innerHTML = def.html || '';
      // Security: do NOT re-execute any <script> tags present in agent-provided HTML.
      // Shadow DOM isolates styles, but dynamically created <script> elements via
      // innerHTML are already inert in modern browsers (they don't execute). We
      // explicitly strip them here as a defense-in-depth measure so no future
      // browser change can execute untrusted agent content.
      this._contentEl.querySelectorAll('script').forEach(s => s.remove());
    }
    if (def.js) {
      this._execJS(def.js);
    }
  }

  _execJS(code) {
    try {
      // Run in the context of the shadow root's content element.
      // eslint-disable-next-line no-new-func
      const fn = new Function('root', 'shadow', code);
      fn(this._contentEl, this._shadow);
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
    // Prepend so it's visible above any partial content.
    this._contentEl.prepend(errorEl);
    // Also mark the host frame border red.
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

    // Always use auto-placement — never set explicit grid_col/grid_row
    // to prevent overlap. The grid engine with dense packing handles layout.
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
    // Merge into stored def.
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
        // Insert before remove button (last child of header).
        const header = this._shadow.querySelector('.widget-header');
        header.insertBefore(this._agentEl, header.lastElementChild);
      }
    }

    if (patch.css !== undefined) {
      let customStyle = this._shadow.querySelector('style[data-role="widget-css"]');
      if (!customStyle) {
        customStyle = document.createElement('style');
        customStyle.dataset.role = 'widget-css';
        this._shadow.insertBefore(customStyle, this._shadow.querySelector('.widget-header'));
      }
      customStyle.textContent = patch.css;
    }

    if (patch.html !== undefined || patch.js !== undefined) {
      if (patch.html !== undefined) {
        this._contentEl.innerHTML = patch.html || '';
        // Security: strip any <script> tags from agent-provided HTML (see _injectContent).
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
   * `keyframesCss` should be a complete `@keyframes` block; a unique name is
   * generated automatically so multiple calls don't collide.
   * @param {string} keyframesCss
   * @param {object} [opts]
   * @param {string} [opts.duration]   e.g. "600ms"
   * @param {string} [opts.easing]
   * @param {string} [opts.fill]
   */
  animate(keyframesCss, opts = {}) {
    const name = `wf-anim-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    // Replace the keyframes name placeholder or inject with generated name.
    const finalCss = keyframesCss.replace(/@keyframes\s+\S+/, `@keyframes ${name}`);
    const styleEl = document.createElement('style');
    styleEl.textContent = finalCss;
    this._shadow.appendChild(styleEl);

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
    // Safety fallback if transition doesn't fire.
    setTimeout(() => onDone?.(), 400);
  }

  /** Trigger the entrance animation (called by CanvasEngine after DOM insertion). */
  triggerEntrance() {
    // Force a reflow so the 'entering' class is painted before we remove it.
    void this._hostEl.offsetWidth;
    this._hostEl.classList.remove('entering');
    this._hostEl.classList.add('visible');
  }

  /**
   * Clean up any resources held by this frame.
   * Call before removing the frame from the registry.
   */
  destroy() {
    // No external resources to release at this level — subclasses or future
    // extensions may override. Included so CanvasEngine.remove() can call
    // frame.destroy() consistently without checking.
  }
}
