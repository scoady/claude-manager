/** REST API client */

const BASE = '';  // same-origin

export const api = {
  // ─── Projects ──────────────────────────────────────────────────────────────

  async getProjects() {
    const res = await fetch(`${BASE}/api/projects`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async createProject(name, description, model) {
    const body = { name, description };
    if (model) body.model = model;
    const res = await fetch(`${BASE}/api/projects`, {
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

  async deleteProject(name) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return true;
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

  // ─── Orchestrator ──────────────────────────────────────────────────────────

  async ensureOrchestrator(projectName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/orchestrator`, { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
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

  // ─── Skills ───────────────────────────────────────────────────────────────

  async getSkills() {
    const res = await fetch(`${BASE}/api/skills`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async getMarketplaceSkills() {
    const res = await fetch(`${BASE}/api/skills/marketplace`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async createSkill(data) {
    const res = await fetch(`${BASE}/api/skills`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async getProjectSkills(projectName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/skills`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async enableProjectSkill(projectName, skillName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/skills/${encodeURIComponent(skillName)}/enable`, {
      method: 'POST',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async disableProjectSkill(projectName, skillName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/skills/${encodeURIComponent(skillName)}/disable`, {
      method: 'POST',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  // ─── Tasks ────────────────────────────────────────────────────────────────

  async getTasks(projectName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/tasks`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async addTask(projectName, text) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async updateTask(projectName, taskIndex, status) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/tasks/${taskIndex}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async deleteTask(projectName, taskIndex) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/tasks/${taskIndex}`, {
      method: 'DELETE',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async planTask(projectName, text, model) {
    const body = { text };
    if (model) body.model = model;
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/tasks/plan`, {
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

  async startTask(projectName, taskIndex, model) {
    const body = model ? { model, task: '' } : { task: '' };
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/tasks/${taskIndex}/start`, {
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

  // ─── Milestones ──────────────────────────────────────────────────────────

  async getMilestones(projectName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/milestones`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async deleteMilestone(projectName, milestoneId) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/milestones/${encodeURIComponent(milestoneId)}`, {
      method: 'DELETE',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async clearMilestones(projectName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/milestones`, {
      method: 'DELETE',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  // ─── Templates ─────────────────────────────────────────────────────────────

  async getTemplates() {
    const res = await fetch(`${BASE}/api/templates`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async getTemplate(templateId) {
    const res = await fetch(`${BASE}/api/templates/${encodeURIComponent(templateId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  // ─── Workflows ─────────────────────────────────────────────────────────────

  async getWorkflow(projectName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/workflow`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async createWorkflow(projectName, team, config, templateId = 'software-engineering') {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/workflow`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_id: templateId, team, config }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async startWorkflow(projectName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/workflow/start`, {
      method: 'POST',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async workflowAction(projectName, action) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/workflow/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async deleteWorkflow(projectName) {
    const res = await fetch(`${BASE}/api/projects/${encodeURIComponent(projectName)}/workflow`, {
      method: 'DELETE',
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return true;
  },

  // ─── Roles ───────────────────────────────────────────────────────────────

  async getRoles() {
    const res = await fetch(`${BASE}/api/roles`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async createRole(data) {
    const res = await fetch(`${BASE}/api/roles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async updateRole(roleId, data) {
    const res = await fetch(`${BASE}/api/roles/${encodeURIComponent(roleId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async deleteRole(roleId) {
    const res = await fetch(`${BASE}/api/roles/${encodeURIComponent(roleId)}`, {
      method: 'DELETE',
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return true;
  },

  async getAllRoles(templateId) {
    const url = templateId
      ? `${BASE}/api/roles/all?template_id=${encodeURIComponent(templateId)}`
      : `${BASE}/api/roles/all`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  // ─── Artifacts ──────────────────────────────────────────────────────────────

  async listFiles(projectName, path = '') {
    const url = `${BASE}/api/projects/${encodeURIComponent(projectName)}/files?path=${encodeURIComponent(path)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async readFile(projectName, path) {
    const url = `${BASE}/api/projects/${encodeURIComponent(projectName)}/files/content?path=${encodeURIComponent(path)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async getGitStatus(projectName) {
    const url = `${BASE}/api/projects/${encodeURIComponent(projectName)}/files/status`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  // ─── Dashboard Controller ───────────────────────────────────────────────────

  async setupDashboard(projectName, prompt) {
    const res = await fetch(`${BASE}/api/canvas/${encodeURIComponent(projectName)}/controller`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  // ─── Settings ──────────────────────────────────────────────────────────────

  async getStats() {
    const res = await fetch(`${BASE}/api/stats`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },
};
