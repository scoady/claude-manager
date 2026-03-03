/** WebSocket client with auto-reconnect */

const WS_URL = `ws://${location.host}/ws`;
const PING_INTERVAL = 25_000;
const RECONNECT_BASE = 1_000;
const RECONNECT_MAX  = 30_000;

export class WSClient {
  constructor(handlers = {}) {
    this._handlers = handlers;
    this._ws = null;
    this._pingTimer = null;
    this._reconnectTimer = null;
    this._attempt = 0;
    this._closed = false;
    this.connect();
  }

  connect() {
    if (this._closed) return;
    try {
      this._ws = new WebSocket(WS_URL);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = () => {
      this._attempt = 0;
      this._handlers.onOpen?.();
      this._startPing();
    };

    this._ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        this._handlers.onMessage?.(msg);
      } catch (e) {
        // ignore parse errors
      }
    };

    this._ws.onclose = () => {
      this._stopPing();
      this._handlers.onClose?.();
      this._scheduleReconnect();
    };

    this._ws.onerror = () => {
      this._handlers.onError?.();
    };
  }

  _startPing() {
    this._pingTimer = setInterval(() => {
      if (this._ws?.readyState === WebSocket.OPEN) {
        this._ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, PING_INTERVAL);
  }

  _stopPing() {
    clearInterval(this._pingTimer);
    this._pingTimer = null;
  }

  _scheduleReconnect() {
    if (this._closed) return;
    clearTimeout(this._reconnectTimer);
    const delay = Math.min(RECONNECT_BASE * 2 ** this._attempt, RECONNECT_MAX);
    this._attempt++;
    this._reconnectTimer = setTimeout(() => this.connect(), delay);
    this._handlers.onReconnecting?.(delay);
  }

  close() {
    this._closed = true;
    this._stopPing();
    clearTimeout(this._reconnectTimer);
    this._ws?.close();
  }
}
