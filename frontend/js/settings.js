/** Settings panel — global config, plugins, per-project permissions */

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
      if (tab === 'plugins')  loadPlugins();
      if (tab === 'projects') loadProjects();
    });
  });
}

// ─── Helpers ──────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const icons = { success:'✓', error:'✗', info:'ℹ', warn:'⚠' };
  el.textContent = `${icons[type] ?? '•'} ${msg}`;
  $('toast-container').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 250); }, 3000);
}

// ─── Global settings ──────────────────────────────────────────────────────

let _globalOriginal = null;

export async function loadGlobalSettings() {
  const editor = $('global-settings-editor');
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
  const editor   = $('global-settings-editor');
  const saveBtn  = $('global-settings-save');
  const resetBtn = $('global-settings-reset');

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
        <div class="plugin-icon">${escHtml(p.id.split('@')[0].slice(0,2).toUpperCase())}</div>
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
        const id      = checkbox.dataset.pluginId;
        const enabled = checkbox.checked;
        const action  = enabled ? 'enable' : 'disable';
        try {
          const res = await fetch(`/api/settings/plugins/${encodeURIComponent(id)}/${action}`, {
            method: 'POST',
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          toast(`Plugin ${id.split('@')[0]} ${action}d`, 'success');
        } catch (e) {
          checkbox.checked = !enabled;  // revert
          toast(`Failed: ${e.message}`, 'error');
        }
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="settings-loading">Error: ${escHtml(e.message)}</div>`;
  }
}

// ─── Project permissions ──────────────────────────────────────────────────

async function loadProjects() {
  const container = $('projects-settings-list');
  container.innerHTML = '<div class="settings-loading">Loading…</div>';
  try {
    const res      = await fetch('/api/settings/projects');
    const projects = await res.json();
    if (!projects.length) {
      container.innerHTML = '<div class="settings-loading">No projects found</div>';
      return;
    }
    container.innerHTML = projects.map(p => renderProjectCard(p)).join('');

    container.querySelectorAll('.project-settings-header').forEach(header => {
      header.addEventListener('click', () => {
        const body = header.nextElementSibling;
        body.classList.toggle('open');
      });
    });

    container.querySelectorAll('.perm-delete-btn').forEach(btn => {
      btn.addEventListener('click', async e => {
        e.stopPropagation();
        const { projectKey, kind, perm } = btn.dataset;
        try {
          const res = await fetch(
            `/api/settings/projects/${encodeURIComponent(projectKey)}/permissions/${kind}/${encodeURIComponent(perm)}`,
            { method: 'DELETE' }
          );
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          btn.closest('.perm-item').remove();
          toast('Permission removed', 'success');
        } catch (e) {
          toast(`Failed: ${e.message}`, 'error');
        }
      });
    });

    container.querySelectorAll('.perm-add-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const { projectKey, kind } = btn.dataset;
        const input = btn.previousElementSibling;
        const permission = input.value.trim();
        if (!permission) return;

        try {
          const res = await fetch(
            `/api/settings/projects/${encodeURIComponent(projectKey)}/permissions/${kind}`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ permission }),
            }
          );
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          input.value = '';
          toast('Permission added', 'success');
          // Refresh the card
          await refreshProjectCard(projectKey, container);
        } catch (e) {
          toast(`Failed: ${e.message}`, 'error');
        }
      });

      // Enter to add
      btn.previousElementSibling.addEventListener('keydown', e => {
        if (e.key === 'Enter') btn.click();
      });
    });
  } catch (e) {
    container.innerHTML = `<div class="settings-loading">Error: ${escHtml(e.message)}</div>`;
  }
}

async function refreshProjectCard(projectKey, container) {
  try {
    const res = await fetch(`/api/settings/projects/${encodeURIComponent(projectKey)}`);
    const settings = await res.json();
    const allRes = await fetch('/api/settings/projects');
    const projects = await allRes.json();
    const project = projects.find(p => p.key === projectKey);
    if (!project) return;
    project.settings = settings;

    const existing = container.querySelector(`[data-project-key="${escHtml(projectKey)}"]`);
    if (existing) {
      const wasOpen = existing.querySelector('.project-settings-body')?.classList.contains('open');
      existing.outerHTML = renderProjectCard(project);
      const updated = container.querySelector(`[data-project-key="${escHtml(projectKey)}"]`);
      if (wasOpen) updated?.querySelector('.project-settings-body')?.classList.add('open');
    }
  } catch (_) {}
}

function renderProjectCard(p) {
  const allow = p.settings?.permissions?.allow ?? [];
  const deny  = p.settings?.permissions?.deny  ?? [];

  const permList = (items, kind) => {
    if (!items.length && kind === 'deny') return '';
    return `
      <div class="perm-section">
        <div class="perm-section-label ${kind}">${kind === 'allow' ? 'Allowed' : 'Denied'}</div>
        <div class="perm-list">
          ${items.map(perm => `
            <div class="perm-item">
              <span class="perm-item-text" title="${escHtml(perm)}">${escHtml(perm)}</span>
              <button class="perm-delete-btn" title="Remove"
                data-project-key="${escHtml(p.key)}" data-kind="${kind}" data-perm="${escHtml(perm)}">
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                  <path d="M2 2l6 6M8 2l-6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                </svg>
              </button>
            </div>`).join('')}
        </div>
        <div class="perm-add-row">
          <input class="perm-add-input" type="text"
            placeholder="e.g. Bash(npm:*)" />
          <button class="perm-add-btn"
            data-project-key="${escHtml(p.key)}" data-kind="${kind}">+ Add</button>
        </div>
      </div>`;
  };

  return `
    <div class="project-settings-card" data-project-key="${escHtml(p.key)}">
      <div class="project-settings-header">
        <div class="project-settings-title">
          <span class="project-settings-name">${escHtml(p.name)}</span>
          <span class="project-settings-path">${escHtml(p.path)}</span>
        </div>
        <div class="project-settings-badges">
          ${allow.length ? `<span class="perm-badge allow">${allow.length} allowed</span>` : ''}
          ${deny.length  ? `<span class="perm-badge deny">${deny.length} denied</span>`   : ''}
          ${!allow.length && !deny.length ? `<span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono)">no overrides</span>` : ''}
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style="color:var(--text-muted)">
            <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>
      <div class="project-settings-body">
        ${permList(allow, 'allow')}
        ${permList(deny,  'deny')}
      </div>
    </div>`;
}
