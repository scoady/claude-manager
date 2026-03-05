/** Settings panel — global config and plugins */

const $ = id => document.getElementById(id);

// ─── Tab switching ─────────────────────────────────────────────────────────

export function initSettingsTabs() {
  document.querySelectorAll('.settings-nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll('.settings-nav-item').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      $(`settings-tab-${tab}`)?.classList.add('active');
      if (tab === 'plugins') loadPlugins();
      if (tab === 'skills') loadSkills();
      if (tab === 'roles') loadRoles();
    });
  });
}

// ─── Helpers ──────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const icons = { success: '✓', error: '✗', info: 'ℹ', warn: '⚠' };
  el.textContent = `${icons[type] ?? '•'} ${msg}`;
  $('toast-container')?.appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 250); }, 3000);
}

// ─── Global settings ──────────────────────────────────────────────────────

let _globalOriginal = null;

export async function loadGlobalSettings() {
  const editor = $('global-settings-editor');
  if (!editor) return;
  try {
    const res = await fetch('/api/settings/global');
    const data = await res.json();
    _globalOriginal = data;
    editor.value = JSON.stringify(data, null, 2);
    editor.classList.remove('error');
  } catch (e) {
    editor.value = '// Failed to load settings';
    toast('Failed to load global settings', 'error');
  }
}

export function initGlobalSettingsEditor() {
  const editor  = $('global-settings-editor');
  const saveBtn = $('global-settings-save');
  const resetBtn = $('global-settings-reset');
  if (!editor || !saveBtn || !resetBtn) return;

  editor.addEventListener('input', () => {
    try { JSON.parse(editor.value); editor.classList.remove('error'); }
    catch { editor.classList.add('error'); }
  });

  saveBtn.addEventListener('click', async () => {
    let parsed;
    try { parsed = JSON.parse(editor.value); }
    catch { toast('Invalid JSON — fix errors before saving', 'error'); return; }

    saveBtn.textContent = 'Saving…';
    saveBtn.disabled = true;
    try {
      const res = await fetch('/api/settings/global', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(parsed),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      _globalOriginal = parsed;
      toast('Global settings saved', 'success');
    } catch (e) {
      toast(`Save failed: ${e.message}`, 'error');
    } finally {
      saveBtn.textContent = 'Save changes';
      saveBtn.disabled = false;
    }
  });

  resetBtn.addEventListener('click', () => {
    if (_globalOriginal !== null) {
      editor.value = JSON.stringify(_globalOriginal, null, 2);
      editor.classList.remove('error');
    }
  });
}

// ─── Plugins ──────────────────────────────────────────────────────────────

async function loadPlugins() {
  const list = $('plugins-list');
  if (!list) return;
  list.innerHTML = '<div class="settings-loading">Loading…</div>';
  try {
    const res = await fetch('/api/settings/plugins');
    const plugins = await res.json();
    if (!plugins.length) {
      list.innerHTML = '<div class="settings-loading">No plugins installed</div>';
      return;
    }
    list.innerHTML = plugins.map(p => `
      <div class="plugin-card">
        <div class="plugin-icon">${escHtml(p.id.split('@')[0].slice(0, 2).toUpperCase())}</div>
        <div class="plugin-info">
          <div class="plugin-name">${escHtml(p.id.split('@')[0])}</div>
          <div class="plugin-meta">
            <span>${escHtml(p.id.split('@')[1] ?? '')}</span>
            <span>v${escHtml(p.version ?? '—')}</span>
            <span>${escHtml(p.scope ?? '')}</span>
          </div>
        </div>
        <label class="toggle-switch" title="${p.enabled ? 'Disable' : 'Enable'} plugin">
          <input type="checkbox" ${p.enabled ? 'checked' : ''} data-plugin-id="${escHtml(p.id)}" />
          <span class="toggle-track"></span>
        </label>
      </div>`).join('');

    list.querySelectorAll('input[data-plugin-id]').forEach(checkbox => {
      checkbox.addEventListener('change', async () => {
        const id     = checkbox.dataset.pluginId;
        const enabled = checkbox.checked;
        const action  = enabled ? 'enable' : 'disable';
        try {
          const res = await fetch(`/api/settings/plugins/${encodeURIComponent(id)}/${action}`, {
            method: 'POST',
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          toast(`Plugin ${id.split('@')[0]} ${action}d`, 'success');
        } catch (e) {
          checkbox.checked = !enabled;
          toast(`Failed: ${e.message}`, 'error');
        }
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="settings-loading">Error: ${escHtml(e.message)}</div>`;
  }
}

// ─── Skills ──────────────────────────────────────────────────────────────

let _skillsLoaded = false;

async function loadSkills() {
  await Promise.all([loadMySkills(), loadMarketplace()]);
  if (!_skillsLoaded) {
    initSkillCreator();
    _skillsLoaded = true;
  }
}

async function loadMySkills() {
  const list = $('skills-list');
  if (!list) return;
  list.innerHTML = '<div class="settings-loading">Loading…</div>';
  try {
    const res = await fetch('/api/skills');
    const skills = await res.json();
    if (!skills.length) {
      list.innerHTML = '<div class="settings-loading">No global skills yet. Create one below.</div>';
      return;
    }
    list.innerHTML = skills.map(s => `
      <div class="plugin-card">
        <div class="plugin-icon skill-icon">${escHtml(s.name.slice(0, 2).toUpperCase())}</div>
        <div class="plugin-info">
          <div class="plugin-name">${escHtml(s.name)}</div>
          <div class="plugin-meta">
            <span>${escHtml(s.description || 'No description')}</span>
          </div>
          <div class="plugin-meta">
            <span>${escHtml(s.path)}</span>
          </div>
        </div>
      </div>`).join('');
  } catch (e) {
    list.innerHTML = `<div class="settings-loading">Error: ${escHtml(e.message)}</div>`;
  }
}

let _allMarketplacePlugins = [];

async function loadMarketplace() {
  const grid = $('marketplace-grid');
  if (!grid) return;
  grid.innerHTML = '<div class="settings-loading">Loading…</div>';
  try {
    const res = await fetch('/api/skills/marketplace');
    _allMarketplacePlugins = await res.json();
    renderMarketplace(_allMarketplacePlugins);
    // Bind filter
    const search = $('marketplace-search');
    if (search && !search.dataset.bound) {
      search.dataset.bound = '1';
      search.addEventListener('input', () => {
        const q = search.value.toLowerCase();
        const filtered = _allMarketplacePlugins.filter(p =>
          p.name.toLowerCase().includes(q) ||
          (p.description || '').toLowerCase().includes(q) ||
          p.marketplace.toLowerCase().includes(q)
        );
        renderMarketplace(filtered);
      });
    }
  } catch (e) {
    grid.innerHTML = `<div class="settings-loading">Error: ${escHtml(e.message)}</div>`;
  }
}

function renderMarketplace(plugins) {
  const grid = $('marketplace-grid');
  if (!grid) return;
  if (!plugins.length) {
    grid.innerHTML = '<div class="settings-loading">No plugins found</div>';
    return;
  }

  // Group by marketplace
  const official = plugins.filter(p => p.marketplace === 'claude-plugins-official');
  const community = plugins.filter(p => p.marketplace !== 'claude-plugins-official');

  let html = '';
  if (official.length) {
    html += '<div class="marketplace-group-label">Official</div>';
    html += '<div class="marketplace-cards">' + official.map(mktCard).join('') + '</div>';
  }
  if (community.length) {
    html += '<div class="marketplace-group-label">Community</div>';
    html += '<div class="marketplace-cards">' + community.map(mktCard).join('') + '</div>';
  }
  grid.innerHTML = html;
}

function mktCard(p) {
  return `
    <div class="marketplace-card${p.installed ? ' installed' : ''}">
      <div class="marketplace-card-icon">${escHtml(p.name.slice(0, 2).toUpperCase())}</div>
      <div class="marketplace-card-body">
        <div class="marketplace-card-name">${escHtml(p.name)}</div>
        <div class="marketplace-card-desc">${escHtml(p.description || 'No description')}</div>
        <div class="marketplace-card-meta">${escHtml(p.marketplace)}</div>
      </div>
      <div class="marketplace-card-status">
        ${p.installed
          ? '<span class="marketplace-installed-badge">Installed</span>'
          : ''}
      </div>
    </div>`;
}

// ─── Roles ──────────────────────────────────────────────────────────────

let _rolesLoaded = false;

async function loadRoles() {
  await loadRolesList();
  if (!_rolesLoaded) {
    initRoleCreator();
    _rolesLoaded = true;
  }
}

async function loadRolesList() {
  const list = $('roles-list');
  if (!list) return;
  list.innerHTML = '<div class="settings-loading">Loading…</div>';
  try {
    // Load all roles (built-in + custom)
    const res = await fetch('/api/roles/all');
    const roles = await res.json();
    if (!roles.length) {
      list.innerHTML = '<div class="settings-loading">No roles defined. Create one below or use a workflow template.</div>';
      return;
    }
    list.innerHTML = roles.map(r => `
      <div class="role-card${r.builtin ? ' role-builtin' : ''}">
        <div class="role-card-header">
          <div class="role-card-icon">${escHtml(r.label.slice(0, 2).toUpperCase())}</div>
          <div class="role-card-info">
            <div class="role-card-name">${escHtml(r.label)}</div>
            <div class="role-card-id">${escHtml(r.role)}</div>
          </div>
          <div class="role-card-badges">
            ${r.builtin ? '<span class="role-badge role-badge-builtin">built-in</span>' : '<span class="role-badge role-badge-custom">custom</span>'}
            ${r.is_worker ? '<span class="role-badge role-badge-worker">worker</span>' : '<span class="role-badge role-badge-coordinator">coordinator</span>'}
          </div>
          ${!r.builtin ? `<button class="role-delete-btn" data-role="${escHtml(r.role)}" title="Delete role">&times;</button>` : ''}
        </div>
        ${r.persona ? `<div class="role-card-persona">${escHtml(r.persona)}</div>` : ''}
        ${r.expertise?.length ? `<div class="role-card-tags">${r.expertise.map(t => `<span class="role-tag">${escHtml(t)}</span>`).join('')}</div>` : ''}
      </div>`).join('');

    // Bind delete buttons
    list.querySelectorAll('.role-delete-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const roleId = btn.dataset.role;
        if (!confirm(`Delete role "${roleId}"?`)) return;
        try {
          await fetch(`/api/roles/${encodeURIComponent(roleId)}`, { method: 'DELETE' });
          toast(`Role "${roleId}" deleted`, 'success');
          await loadRolesList();
        } catch (e) {
          toast(`Failed: ${e.message}`, 'error');
        }
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="settings-loading">Error: ${escHtml(e.message)}</div>`;
  }
}

function initRoleCreator() {
  const idEl = $('role-create-id');
  const labelEl = $('role-create-label');
  const personaEl = $('role-create-persona');
  const expertiseEl = $('role-create-expertise');
  const workerEl = $('role-create-worker');
  const createBtn = $('role-create-btn');
  if (!idEl || !createBtn) return;

  createBtn.addEventListener('click', async () => {
    const role = idEl.value.trim();
    const label = labelEl.value.trim();
    if (!role) { toast('Role ID is required', 'error'); return; }
    if (!label) { toast('Display name is required', 'error'); return; }

    const expertise = expertiseEl.value.trim()
      ? expertiseEl.value.split(',').map(s => s.trim()).filter(Boolean)
      : [];

    createBtn.disabled = true;
    createBtn.textContent = 'Creating…';
    try {
      await fetch('/api/roles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role,
          label,
          persona: personaEl.value.trim(),
          expertise,
          is_worker: workerEl.checked,
        }),
      });
      toast(`Role "${label}" created`, 'success');
      idEl.value = '';
      labelEl.value = '';
      personaEl.value = '';
      expertiseEl.value = '';
      workerEl.checked = true;
      await loadRolesList();
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    } finally {
      createBtn.disabled = false;
      createBtn.textContent = 'Create Role';
    }
  });
}

function initSkillCreator() {
  const nameEl  = $('skill-create-name');
  const descEl  = $('skill-create-desc');
  const bodyEl  = $('skill-create-body');
  const scopeEl = $('skill-create-scope');
  const preview = $('skill-preview-code');
  const createBtn = $('skill-create-btn');
  if (!nameEl || !createBtn) return;

  // Populate scope select
  fetch('/api/projects').then(r => r.json()).then(projects => {
    scopeEl.innerHTML = '<option value="global">Global</option>' +
      projects.map(p => `<option value="${escHtml(p.name)}">${escHtml(p.name)}</option>`).join('');
  }).catch(() => {
    scopeEl.innerHTML = '<option value="global">Global</option>';
  });

  // Live preview
  function updatePreview() {
    const tools = Array.from(
      document.querySelectorAll('#skill-tools-checkboxes input:checked')
    ).map(c => c.value);
    let yaml = '---\n';
    yaml += `name: ${nameEl.value || ''}\n`;
    yaml += `description: ${descEl.value || ''}\n`;
    if (tools.length) yaml += `allowed-tools: ${tools.join(', ')}\n`;
    yaml += '---\n';
    if (bodyEl.value) yaml += '\n' + bodyEl.value;
    preview.textContent = yaml;
  }
  nameEl.addEventListener('input', updatePreview);
  descEl.addEventListener('input', updatePreview);
  bodyEl.addEventListener('input', updatePreview);
  document.querySelectorAll('#skill-tools-checkboxes input').forEach(c =>
    c.addEventListener('change', updatePreview)
  );

  // Create
  createBtn.addEventListener('click', async () => {
    const name = nameEl.value.trim();
    const description = descEl.value.trim();
    const content = bodyEl.value.trim();
    if (!name) { toast('Name is required', 'error'); return; }
    if (!description) { toast('Description is required', 'error'); return; }
    if (!content) { toast('Body content is required', 'error'); return; }

    const allowed_tools = Array.from(
      document.querySelectorAll('#skill-tools-checkboxes input:checked')
    ).map(c => c.value);

    createBtn.disabled = true;
    createBtn.textContent = 'Creating…';
    try {
      await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description, content, allowed_tools, scope: scopeEl.value }),
      });
      toast(`Skill "${name}" created`, 'success');
      nameEl.value = '';
      descEl.value = '';
      bodyEl.value = '';
      document.querySelectorAll('#skill-tools-checkboxes input').forEach(c => c.checked = false);
      updatePreview();
      await loadMySkills();
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    } finally {
      createBtn.disabled = false;
      createBtn.textContent = 'Create Skill';
    }
  });
}
