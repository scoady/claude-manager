/** REST API client */

const BASE = '';  // same-origin

export const api = {
  // ─── Projects ──────────────────────────────────────────────────────────────

  async getProjects() {
    const res = await fetch(`${BASE}/api/projects`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async createProject(name, description) {
    const res = await fetch(`${BASE}/api/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async getProject(name) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async updateProjectConfig(name, config) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(name)}/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async dispatchTask(name, task, model) {
    const body = { task };
    if (model) body.model = model;
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(name)}/dispatch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  // ─── Agents ────────────────────────────────────────────────────────────────

  async getAgents() {
    const res = await fetch(`${BASE}/api/agents`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async getMessages(sessionId) {
    const res = await fetch(`${BASE}/api/agents/${encodeURIComponent(sessionId)}/messages`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async injectMessage(sessionId, message) {
    const res = await fetch(`${BASE}/api/agents/${encodeURIComponent(sessionId)}/inject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async killAgent(sessionId) {
    const res = await fetch(`${BASE}/api/agents/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    });
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
    return true;
  },

  // ─── Settings ──────────────────────────────────────────────────────────────

  async getStats() {
    const res = await fetch(`${BASE}/api/stats`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },
};
