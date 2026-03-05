/**
 * CanvasEngine — manages the full lifecycle of canvas widgets rendered into
 * a CSS Grid host element.
 *
 * Design constraints:
 *  - All layout changes use CSS transitions: cubic-bezier(0.22, 1, 0.36, 1), 350ms
 *  - create() triggers an entrance animation: slide-up + fade-in
 *  - If multiple widgets arrive within 50ms they are staggered 60ms apart
 *  - Each widget is isolated in its own Shadow DOM (WidgetFrame)
 *  - Grid placement via grid_col / grid_row / col_span / row_span (1-indexed)
 */

import { WidgetFrame } from './WidgetFrame.js';
import { WidgetRegistry } from './WidgetRegistry.js';

const STAGGER_WINDOW_MS = 50;   // batch window for entrance stagger
const STAGGER_DELAY_MS  = 60;   // additional delay per widget in a batch

export class CanvasEngine {
  /**
   * @param {HTMLElement|null} [hostEl]  — the `<div id="canvas-root">` element.
   *   If omitted, the engine waits until `mount(hostEl)` is called.
   */
  constructor(hostEl = null) {
    this._host    = null;
    this._registry = new WidgetRegistry();

    // Pending entrance stagger state.
    this._pendingBatch = [];   // { frame, widgetId }[]
    this._batchTimer   = null;

    // Store bound references so we can remove them in destroy().
    this._boundMouseMove = this._onMouseMove.bind(this);
    this._boundMouseUp   = this._onMouseUp.bind(this);

    if (hostEl) {
      this.mount(hostEl);
    }
  }

  /**
   * Attach the engine to a host DOM element.
   * Can be called lazily after construction.
   * @param {HTMLElement} hostEl
   */
  mount(hostEl) {
    this._host = hostEl;
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  /**
   * Create a new widget and add it to the canvas.
   *
   * @param {object} widgetDef
   * @param {string}  widgetDef.widget_id   — required unique ID
   * @param {string}  [widgetDef.title]
   * @param {string}  [widgetDef.agent]     — agent attribution string
   * @param {string}  [widgetDef.html]
   * @param {string}  [widgetDef.css]
   * @param {string}  [widgetDef.js]
   * @param {number}  [widgetDef.grid_col]  — 1-indexed column start
   * @param {number}  [widgetDef.grid_row]  — 1-indexed row start
   * @param {number}  [widgetDef.col_span]  — default 1
   * @param {number}  [widgetDef.row_span]  — default 1
   */
  create(widgetDef) {
    if (!widgetDef?.widget_id) {
      console.warn('[CanvasEngine] create() called without widget_id, skipping.');
      return;
    }

    // Idempotent: if widget already exists, treat as an update.
    if (this._registry.get(widgetDef.widget_id)) {
      this.update(widgetDef.widget_id, widgetDef);
      return;
    }

    const frame = new WidgetFrame({
      ...widgetDef,
      onRemove: (id) => this.remove(id),
    });

    this._registry.set(widgetDef.widget_id, frame);

    if (this._host) {
      this._host.appendChild(frame.element);
      this._scheduleEntrance(frame, widgetDef.widget_id);
      this._hideEmptyState();
    }
  }

  /**
   * Apply a partial patch to an existing widget.
   * @param {string} widgetId
   * @param {object} patch
   */
  update(widgetId, patch) {
    const frame = this._registry.get(widgetId);
    if (!frame) {
      console.warn(`[CanvasEngine] update() — widget "${widgetId}" not found.`);
      return;
    }
    frame.update(patch);
  }

  /**
   * Remove a widget from the canvas with an exit animation.
   * @param {string} widgetId
   */
  remove(widgetId) {
    const frame = this._registry.get(widgetId);
    if (!frame) return;

    // Destroy frame resources before removing from registry.
    frame.destroy();
    this._registry.delete(widgetId);

    frame.remove(() => {
      frame.element.remove();
      if (this._registry.size === 0) {
        this._showEmptyState();
      }
    });
  }

  /**
   * Trigger a CSS keyframe animation on a specific widget's host element.
   * @param {string} widgetId
   * @param {string} keyframesCss  — complete `@keyframes` block
   * @param {object} [opts]
   */
  animate(widgetId, keyframesCss, opts = {}) {
    const frame = this._registry.get(widgetId);
    if (!frame) return;
    frame.animate(keyframesCss, opts);
  }

  /**
   * Remove all widgets immediately (no exit animation).
   */
  clear() {
    for (const frame of this._registry.getAll()) {
      frame.destroy();
      frame.element.remove();
    }
    this._registry.clear();
    if (this._host) {
      this._showEmptyState();
    }
  }

  /**
   * Tear down the engine: remove all widgets and detach any document listeners.
   */
  destroy() {
    this.clear();
    document.removeEventListener('mousemove', this._boundMouseMove);
    document.removeEventListener('mouseup',   this._boundMouseUp);
  }

  // ── Mouse handlers (stored bound for proper removeEventListener) ─────────────

  _onMouseMove(e) {
    // Reserved for future drag-to-reposition support.
    void e;
  }

  _onMouseUp(e) {
    // Reserved for future drag-to-reposition support.
    void e;
  }

  // ── Entrance stagger ─────────────────────────────────────────────────────────

  /**
   * Collect widgets that arrive within STAGGER_WINDOW_MS into a batch and
   * trigger their entrance animations with STAGGER_DELAY_MS offsets.
   */
  _scheduleEntrance(frame, widgetId) {
    this._pendingBatch.push({ frame, widgetId });

    if (this._batchTimer !== null) {
      clearTimeout(this._batchTimer);
    }

    this._batchTimer = setTimeout(() => {
      this._batchTimer = null;
      const batch = this._pendingBatch.splice(0);
      batch.forEach(({ frame: f }, index) => {
        setTimeout(() => {
          f.triggerEntrance();
        }, index * STAGGER_DELAY_MS);
      });
    }, STAGGER_WINDOW_MS);
  }

  // ── Empty state helpers ───────────────────────────────────────────────────────

  _hideEmptyState() {
    const el = this._host?.querySelector('.canvas-empty-state');
    if (el) el.style.display = 'none';
  }

  _showEmptyState() {
    const el = this._host?.querySelector('.canvas-empty-state');
    if (el) el.style.display = '';
  }
}
