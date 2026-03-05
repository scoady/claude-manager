/** WidgetRegistry — simple Map-based store for WidgetFrame instances */

export class WidgetRegistry {
  constructor() {
    this._map = new Map();
  }

  /**
   * Store a frame by widget ID.
   * @param {string} id
   * @param {import('./WidgetFrame.js').WidgetFrame} frame
   */
  set(id, frame) {
    this._map.set(id, frame);
  }

  /**
   * Retrieve a frame by widget ID.
   * @param {string} id
   * @returns {import('./WidgetFrame.js').WidgetFrame|undefined}
   */
  get(id) {
    return this._map.get(id);
  }

  /**
   * Remove a frame by widget ID.
   * @param {string} id
   */
  delete(id) {
    this._map.delete(id);
  }

  /**
   * Iterate over all stored frames.
   * @returns {IterableIterator<import('./WidgetFrame.js').WidgetFrame>}
   */
  getAll() {
    return this._map.values();
  }

  /** Remove all stored frames. */
  clear() {
    this._map.clear();
  }

  /** @returns {number} */
  get size() {
    return this._map.size;
  }
}
