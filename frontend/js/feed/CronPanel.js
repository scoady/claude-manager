/** CronPanel — scheduled task management with mission-control aesthetic. */
import { escapeHtml, toast } from '../utils.js';
import { api } from '../api.js';

const PRESETS = [
  { label: 'Every 5 min',  cron: '*/5 * * * *' },
  { label: 'Every 15 min', cron: '*/15 * * * *' },
  { label: 'Every hour',   cron: '0 * * * *' },
  { label: 'Every 2 hours',cron: '0 */2 * * *' },
  { label: 'Every 6 hours',cron: '0 */6 * * *' },
  { label: 'Daily (00:00)',cron: '0 0 * * *' },
  { label: 'Daily (09:00)',cron: '0 9 * * *' },
  { label: 'Weekly (Mon)', cron: '0 0 * * 1' },
];

function humanSchedule(cron) {
  const map = {
    '*/5 * * * *':   'Every 5 minutes',
    '*/10 * * * *':  'Every 10 minutes',
    '*/15 * * * *':  'Every 15 minutes',
    '*/30 * * * *':  'Every 30 minutes',
    '0 * * * *':     'Every hour',
    '0 */2 * * *':   'Every 2 hours',
    '0 */3 * * *':   'Every 3 hours',
    '0 */6 * * *':   'Every 6 hours',
    '0 */12 * * *':  'Every 12 hours',
    '0 0 * * *':     'Daily at midnight',
    '0 9 * * *':     'Daily at 09:00',
    '0 0 * * 1':     'Weekly on Monday',
    '0 0 * * 0':     'Weekly on Sunday',
    '0 0 1 * *':     'Monthly (1st)',
  };
  return map[cron] || cron;
}

function relativeTime(isoStr) {
  if (!isoStr) return 'never';
  const d = new Date(isoStr);
  const now = Date.now();
  const diff = d.getTime() - now;
  const abs = Math.abs(diff);

  if (abs < 60_000) {
    const s = Math.round(abs / 1000);
    return diff < 0 ? `${s}s ago` : `in ${s}s`;
  }
  if (abs < 3_600_000) {
    const m = Math.round(abs / 60_000);
    return diff < 0 ? `${m}m ago` : `in ${m}m`;
  }
  if (abs < 86_400_000) {
    const h = Math.round(abs / 3_600_000);
    return diff < 0 ? `${h}h ago` : `in ${h}h`;
  }
  const days = Math.round(abs / 86_400_000);
  return diff < 0 ? `${days}d ago` : `in ${days}d`;
}

function secondsUntil(isoStr) {
  if (!isoStr) return Infinity;
  return (new Date(isoStr).getTime() - Date.now()) / 1000;
}

export class CronPanel {
  constructor(projectName) {
    this._project = projectName;
    this._jobs = [];
    this._el = document.createElement('div');
    this._el.className = 'cron-panel';
    this._formExpanded = false;
    this._refreshTimer = null;
    this._selectedPreset = null;
    this._render();
  }

  get el() { return this._el; }

  async load() {
    try {
      this._jobs = await api.getCronJobs(this._project);
    } catch (e) {
      this._jobs = [];
      console.error('Failed to load cron jobs:', e);
    }
    this._renderContent();
    this._startAutoRefresh();
  }

  destroy() {
    this._stopAutoRefresh();
  }

  _startAutoRefresh() {
    this._stopAutoRefresh();
    this._refreshTimer = setInterval(() => this._refreshJobs(), 10_000);
  }

  _stopAutoRefresh() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  stopAutoRefresh() { this._stopAutoRefresh(); }

  async _refreshJobs() {
    try {
      this._jobs = await api.getCronJobs(this._project);
      this._renderContent();
    } catch (_) {}
  }

  // ── Skeleton ─────────────────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="cron-body"></div>
    `;
  }

  _renderContent() {
    const body = this._el.querySelector('.cron-body');
    if (!body) return;

    const sortedJobs = [...this._jobs].sort((a, b) => {
      // enabled first, then by next_run
      if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
      return (a.next_run || '').localeCompare(b.next_run || '');
    });

    body.innerHTML = `
      ${this._renderTimeline(sortedJobs)}
      ${this._renderCreateForm()}
      ${this._renderJobList(sortedJobs)}
    `;

    this._bindFormEvents(body);
    this._bindJobEvents(body);
    this._startTimelineAnimation(body);
  }

  // ── Timeline ─────────────────────────────────────────────────────────────

  _renderTimeline(jobs) {
    const enabledJobs = jobs.filter(j => j.enabled && j.next_run);
    if (!enabledJobs.length) {
      return `
        <div class="cron-timeline">
          <div class="cron-timeline-header">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <circle cx="7" cy="7" r="5.5" stroke="currentColor" stroke-width="1.2"/>
              <path d="M7 4v3.5l2 1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <span>Schedule Timeline</span>
          </div>
          <div class="cron-timeline-empty">No scheduled jobs</div>
        </div>
      `;
    }

    // Show next 12 hours of scheduled runs
    const now = Date.now();
    const windowMs = 12 * 3600 * 1000;
    const dots = [];

    for (const job of enabledJobs) {
      const nextMs = new Date(job.next_run).getTime();
      if (nextMs > now && nextMs < now + windowMs) {
        const pct = ((nextMs - now) / windowMs) * 100;
        const secsUntil = (nextMs - now) / 1000;
        const imminent = secsUntil < 60;
        dots.push(`
          <div class="cron-timeline-dot${imminent ? ' imminent' : ''}"
               style="left: ${pct}%"
               title="${escapeHtml(job.name)} - ${relativeTime(job.next_run)}">
            <div class="cron-dot-pip"></div>
            ${imminent ? '<div class="cron-dot-pulse"></div>' : ''}
            <div class="cron-dot-label">${escapeHtml(job.name)}</div>
          </div>
        `);
      }
    }

    // Time axis labels
    const hours = [0, 1, 2, 3, 6, 12];
    const ticks = hours.map(h => {
      const pct = (h / 12) * 100;
      const label = h === 0 ? 'Now' : `+${h}h`;
      return `<span class="cron-timeline-tick" style="left: ${pct}%">${label}</span>`;
    }).join('');

    return `
      <div class="cron-timeline">
        <div class="cron-timeline-header">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <circle cx="7" cy="7" r="5.5" stroke="currentColor" stroke-width="1.2"/>
            <path d="M7 4v3.5l2 1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span>Schedule Timeline</span>
          <span class="cron-timeline-window">Next 12 hours</span>
        </div>
        <div class="cron-timeline-track">
          <div class="cron-timeline-line"></div>
          <div class="cron-timeline-now"></div>
          ${dots.join('')}
        </div>
        <div class="cron-timeline-axis">${ticks}</div>
      </div>
    `;
  }

  _startTimelineAnimation(root) {
    // Animate imminent dots
    const imminentDots = root.querySelectorAll('.cron-timeline-dot.imminent');
    imminentDots.forEach(dot => {
      dot.classList.add('animate');
    });
  }

  // ── Create form ──────────────────────────────────────────────────────────

  _renderCreateForm() {
    return `
      <div class="cron-create-section${this._formExpanded ? ' expanded' : ''}">
        <button class="cron-create-toggle">
          <svg class="cron-create-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none">
            <path d="M3 2l4 3-4 3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span>Create Scheduled Job</span>
        </button>
        <div class="cron-create-form">
          <div class="cron-form-group">
            <label class="cron-form-label">Name</label>
            <input type="text" class="cron-form-input cron-name-input" placeholder="e.g. Nightly test run" spellcheck="false" />
          </div>
          <div class="cron-form-group">
            <label class="cron-form-label">Schedule</label>
            <div class="cron-preset-grid">
              ${PRESETS.map(p => `
                <button class="cron-preset-btn" data-cron="${escapeHtml(p.cron)}">${escapeHtml(p.label)}</button>
              `).join('')}
            </div>
            <input type="text" class="cron-form-input cron-schedule-input" placeholder="Or enter cron expression: */5 * * * *" spellcheck="false" />
            <span class="cron-schedule-preview"></span>
          </div>
          <div class="cron-form-group">
            <label class="cron-form-label">Task Prompt</label>
            <textarea class="cron-form-textarea cron-task-input" rows="3" placeholder="The prompt to dispatch to an agent when triggered..."></textarea>
          </div>
          <div class="cron-form-actions">
            <button class="cron-create-btn" disabled>
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M6 2v8M2 6h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
              Create Job
            </button>
          </div>
        </div>
      </div>
    `;
  }

  // ── Job list ─────────────────────────────────────────────────────────────

  _renderJobList(jobs) {
    if (!jobs.length) {
      return `
        <div class="cron-empty">
          <svg width="36" height="36" viewBox="0 0 36 36" fill="none" opacity="0.3">
            <circle cx="18" cy="18" r="14" stroke="currentColor" stroke-width="1.5"/>
            <path d="M18 10v9l5 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <div class="cron-empty-text">No scheduled jobs yet</div>
          <div class="cron-empty-sub">Create a job to automate agent tasks on a schedule</div>
        </div>
      `;
    }

    return `
      <div class="cron-job-list">
        ${jobs.map(j => this._renderJobCard(j)).join('')}
      </div>
    `;
  }

  _renderJobCard(job) {
    const enabled = job.enabled;
    const secsUntil = secondsUntil(job.next_run);
    const imminent = enabled && secsUntil > 0 && secsUntil < 60;
    const statusCls = !enabled ? 'paused' : imminent ? 'imminent' : 'active';
    const statusLabel = !enabled ? 'Paused' : imminent ? 'Firing' : 'Active';

    // Sparkline dots for run history (simple visualization using run_count)
    const runCount = job.run_count || 0;
    const sparkDots = Math.min(runCount, 10);
    const sparkline = sparkDots > 0
      ? Array.from({ length: sparkDots }, (_, i) => {
          const opacity = 0.3 + (i / Math.max(sparkDots - 1, 1)) * 0.7;
          return `<span class="cron-spark-dot" style="opacity:${opacity}"></span>`;
        }).join('')
      : '<span class="cron-spark-none">--</span>';

    return `
      <div class="cron-job-card${imminent ? ' imminent' : ''}${!enabled ? ' disabled' : ''}" data-job-id="${escapeHtml(job.id)}">
        <div class="cron-job-header">
          <div class="cron-job-status ${statusCls}">
            <span class="cron-status-dot"></span>
            <span class="cron-status-label">${statusLabel}</span>
          </div>
          <div class="cron-job-name">${escapeHtml(job.name)}</div>
          <div class="cron-job-actions">
            <button class="cron-trigger-btn" data-job-id="${escapeHtml(job.id)}" title="Run now">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M3 2l7 4-7 4V2z" fill="currentColor"/>
              </svg>
            </button>
            <label class="cron-toggle" title="${enabled ? 'Disable' : 'Enable'}">
              <input type="checkbox" class="cron-toggle-input" data-job-id="${escapeHtml(job.id)}" ${enabled ? 'checked' : ''} />
              <span class="cron-toggle-slider"></span>
            </label>
            <button class="cron-delete-btn" data-job-id="${escapeHtml(job.id)}" title="Delete">
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
        </div>
        <div class="cron-job-details">
          <div class="cron-job-schedule">
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
              <circle cx="5.5" cy="5.5" r="4" stroke="currentColor" stroke-width="1"/>
              <path d="M5.5 3v3l1.5 1" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
            </svg>
            <span class="cron-schedule-human">${escapeHtml(humanSchedule(job.schedule))}</span>
            <span class="cron-schedule-raw">${escapeHtml(job.schedule)}</span>
          </div>
          <div class="cron-job-meta">
            <span class="cron-meta-item" title="Last run">
              <span class="cron-meta-label">Last:</span>
              <span class="cron-meta-value">${relativeTime(job.last_run)}</span>
            </span>
            <span class="cron-meta-sep"></span>
            <span class="cron-meta-item" title="Next run">
              <span class="cron-meta-label">Next:</span>
              <span class="cron-meta-value${imminent ? ' imminent' : ''}">${enabled ? relativeTime(job.next_run) : '--'}</span>
            </span>
            <span class="cron-meta-sep"></span>
            <span class="cron-meta-item" title="Total runs">
              <span class="cron-meta-label">Runs:</span>
              <span class="cron-meta-value">${runCount}</span>
            </span>
          </div>
          <div class="cron-job-task" title="${escapeHtml(job.task)}">
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
              <path d="M2 3h7M2 5.5h5M2 8h6" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
            </svg>
            <span>${escapeHtml(job.task.length > 120 ? job.task.slice(0, 120) + '...' : job.task)}</span>
          </div>
          <div class="cron-job-sparkline" title="${runCount} total runs">
            ${sparkline}
          </div>
        </div>
      </div>
    `;
  }

  // ── Events ───────────────────────────────────────────────────────────────

  _bindFormEvents(root) {
    // Toggle create form
    const toggle = root.querySelector('.cron-create-toggle');
    if (toggle) {
      toggle.addEventListener('click', () => {
        this._formExpanded = !this._formExpanded;
        const section = root.querySelector('.cron-create-section');
        section?.classList.toggle('expanded', this._formExpanded);
      });
    }

    // Preset buttons
    root.querySelectorAll('.cron-preset-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        root.querySelectorAll('.cron-preset-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');
        const schedInput = root.querySelector('.cron-schedule-input');
        schedInput.value = btn.dataset.cron;
        this._selectedPreset = btn.dataset.cron;
        this._updateSchedulePreview(root);
        this._updateCreateButton(root);
      });
    });

    // Schedule input change
    const schedInput = root.querySelector('.cron-schedule-input');
    if (schedInput) {
      schedInput.addEventListener('input', () => {
        root.querySelectorAll('.cron-preset-btn').forEach(b => b.classList.remove('selected'));
        this._selectedPreset = null;
        this._updateSchedulePreview(root);
        this._updateCreateButton(root);
      });
    }

    // Name + task inputs
    const nameInput = root.querySelector('.cron-name-input');
    const taskInput = root.querySelector('.cron-task-input');
    [nameInput, taskInput, schedInput].forEach(input => {
      if (input) input.addEventListener('input', () => this._updateCreateButton(root));
    });

    // Create button
    const createBtn = root.querySelector('.cron-create-btn');
    if (createBtn) {
      createBtn.addEventListener('click', () => this._createJob(root));
    }
  }

  _updateSchedulePreview(root) {
    const preview = root.querySelector('.cron-schedule-preview');
    const input = root.querySelector('.cron-schedule-input');
    if (!preview || !input) return;
    const val = input.value.trim();
    if (!val) {
      preview.textContent = '';
      return;
    }
    preview.textContent = humanSchedule(val);
  }

  _updateCreateButton(root) {
    const btn = root.querySelector('.cron-create-btn');
    const name = root.querySelector('.cron-name-input')?.value.trim();
    const schedule = root.querySelector('.cron-schedule-input')?.value.trim();
    const task = root.querySelector('.cron-task-input')?.value.trim();
    if (btn) btn.disabled = !(name && schedule && task);
  }

  async _createJob(root) {
    const name = root.querySelector('.cron-name-input')?.value.trim();
    const schedule = root.querySelector('.cron-schedule-input')?.value.trim();
    const task = root.querySelector('.cron-task-input')?.value.trim();
    if (!name || !schedule || !task) return;

    const btn = root.querySelector('.cron-create-btn');
    if (btn) btn.disabled = true;

    try {
      await api.createCronJob(this._project, { name, schedule, task });
      toast('Cron job created', 'success', 2500);
      this._formExpanded = false;
      this._selectedPreset = null;
      await this._refreshJobs();
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
      if (btn) btn.disabled = false;
    }
  }

  _bindJobEvents(root) {
    // Toggle enable/disable
    root.querySelectorAll('.cron-toggle-input').forEach(input => {
      input.addEventListener('change', async () => {
        const jobId = input.dataset.jobId;
        try {
          await api.updateCronJob(this._project, jobId, { enabled: input.checked });
          await this._refreshJobs();
        } catch (e) {
          toast(`Failed: ${e.message}`, 'error');
          input.checked = !input.checked;
        }
      });
    });

    // Trigger now
    root.querySelectorAll('.cron-trigger-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const jobId = btn.dataset.jobId;
        btn.classList.add('firing');
        try {
          await api.triggerCronJob(this._project, jobId);
          toast('Job triggered', 'success', 2000);
          setTimeout(() => btn.classList.remove('firing'), 600);
          await this._refreshJobs();
        } catch (e) {
          toast(`Failed: ${e.message}`, 'error');
          btn.classList.remove('firing');
        }
      });
    });

    // Delete
    root.querySelectorAll('.cron-delete-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const jobId = btn.dataset.jobId;
        const card = btn.closest('.cron-job-card');
        if (card) card.classList.add('removing');
        try {
          await api.deleteCronJob(this._project, jobId);
          toast('Job deleted', 'success', 2000);
          await this._refreshJobs();
        } catch (e) {
          toast(`Failed: ${e.message}`, 'error');
          if (card) card.classList.remove('removing');
        }
      });
    });
  }
}
