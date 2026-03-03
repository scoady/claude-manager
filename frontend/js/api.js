/** REST API client */

const BASE = '';  // same-origin

export const api = {
  async getAgents() {
    const res = await fetch(`${BASE}/api/agents`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async getMessages(sessionId) {
    const res = await fetch(`${BASE}/api/agents/${sessionId}/messages`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async sendMessage(sessionId, message) {
    const res = await fetch(`${BASE}/api/agents/${sessionId}/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async getStats() {
    const res = await fetch(`${BASE}/api/stats`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },
};
