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
