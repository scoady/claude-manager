"""
Starter widget templates for the Canvas MCP.

Agents can use these as starting points for canvas_put() calls.
Copy-paste the html/css fields into your canvas_put call and customize.

Usage example:
    from backend.mcp.widget_templates import STAT_COUNTER
    await canvas_put(
        widget_id="tasks-done",
        title=STAT_COUNTER["title"],
        html=STAT_COUNTER["html"].replace('--value: "47"', '--value: "128"'),
        css=STAT_COUNTER["css"],
        col_span=STAT_COUNTER["col_span"],
        row_span=STAT_COUNTER["row_span"],
    )

CSS custom properties available per template:
  STAT_COUNTER   : --value (display number), --label (subtitle text)
  SPARKLINE_CHART: no custom props — replace SVG path data and axis labels in html
  LOG_STREAM     : no custom props — replace log line text content in html
  PROGRESS_RING  : --progress (0.0–1.0 float, e.g. 0.72 = 72%)

All templates:
  - Use background: transparent (the WidgetFrame provides the card background)
  - Are self-contained with inline CSS only (no external fetches)
  - Work inside Shadow DOM (no global class conflicts)
  - Use only inline SVG (no <img> tags, no external icons)
  - Assume the host page has already loaded IBM Plex Mono and Instrument Serif
"""

# ---------------------------------------------------------------------------
# STAT_COUNTER
# A metric tile with a large centered number, label, tiny bar sparkline,
# and a pulsing glow ring. Use --value and --label CSS custom properties
# or replace them directly in the html string before calling canvas_put.
# col_span=1, row_span=1 (standard square tile)
# ---------------------------------------------------------------------------
STAT_COUNTER = {
    "title": "Stat Counter",
    "col_span": 1,
    "row_span": 1,
    "description": "A metric tile showing a large number with label and trend indicator",
    "html": """
<div class="stat-root">
  <!-- Edit --value to change the displayed number -->
  <!-- Edit --label to change the subtitle          -->
  <div class="stat-inner" style="--value: '47'; --label: 'tasks done'">
    <div class="stat-number"></div>
    <div class="stat-label"></div>
    <!-- 5-bar sparkline: replace height percentages (10%–90%) with real values -->
    <svg class="sparkline" viewBox="0 0 60 20" preserveAspectRatio="none" aria-hidden="true">
      <rect x="2"  y="10" width="8" height="10" rx="1" fill="#67e8f9" opacity="0.4"/>
      <rect x="14" y="6"  width="8" height="14" rx="1" fill="#67e8f9" opacity="0.4"/>
      <rect x="26" y="13" width="8" height="7"  rx="1" fill="#67e8f9" opacity="0.4"/>
      <rect x="38" y="4"  width="8" height="16" rx="1" fill="#67e8f9" opacity="0.4"/>
      <rect x="50" y="2"  width="8" height="18" rx="1" fill="#67e8f9" opacity="0.4"/>
    </svg>
  </div>
</div>
""",
    "css": """
.stat-root {
  /* transparent — WidgetFrame supplies the card background */
  background: transparent;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  box-sizing: border-box;
  /* Pulsing glow ring */
  box-shadow:
    0 0 0 1px rgba(103,232,249,0.2),
    0 0 20px rgba(103,232,249,0.1);
  border-radius: 10px;
  animation: glow-pulse 3s ease-in-out infinite;
}

@keyframes glow-pulse {
  0%, 100% {
    box-shadow:
      0 0 0 1px rgba(103,232,249,0.2),
      0 0 20px rgba(103,232,249,0.1);
  }
  50% {
    box-shadow:
      0 0 0 1px rgba(103,232,249,0.4),
      0 0 32px rgba(103,232,249,0.22);
  }
}

.stat-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 100%;
}

/* Render --value CSS custom property as visible text via pseudo-element content */
.stat-number::before {
  content: var(--value, "0");
  font-family: 'Instrument Serif', Georgia, serif;
  font-size: 42px;
  color: #67e8f9;
  text-shadow: 0 0 20px rgba(103,232,249,0.5);
  display: block;
  line-height: 1;
  text-align: center;
}

/* Render --label CSS custom property as visible text via pseudo-element content */
.stat-label::before {
  content: var(--label, "metric");
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  display: block;
  text-align: center;
}

.sparkline {
  width: 60px;
  height: 20px;
  margin-top: 4px;
  overflow: visible;
}
""",
}


# ---------------------------------------------------------------------------
# SPARKLINE_CHART
# A wide chart tile with a title bar, SVG area chart with gradient fill,
# and Y-axis tick labels. Replace the SVG path points and axis labels in
# the html field to display real data.
# col_span=2, row_span=1 (wide tile — spans two grid columns)
# ---------------------------------------------------------------------------
SPARKLINE_CHART = {
    "title": "Sparkline Chart",
    "col_span": 2,
    "row_span": 1,
    "description": "Wide area chart with gradient fill, neon line stroke, and Y-axis tick labels",
    "html": """
<div class="chart-root">
  <!-- Title bar — replace "CPU Usage" with your metric name -->
  <div class="chart-header">
    <span class="chart-title">CPU Usage</span>
    <span class="chart-range">last 5 min</span>
  </div>

  <div class="chart-body">
    <!-- Y-axis labels — replace 100/50/0 with your scale -->
    <div class="y-axis">
      <span>100</span>
      <span>50</span>
      <span>0</span>
    </div>

    <svg class="area-chart" viewBox="0 0 200 60" preserveAspectRatio="none">
      <defs>
        <!-- Gradient fill: cyan at 30% opacity → transparent at bottom -->
        <linearGradient id="area-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stop-color="#67e8f9" stop-opacity="0.30"/>
          <stop offset="100%" stop-color="#67e8f9" stop-opacity="0.00"/>
        </linearGradient>
      </defs>

      <!--
        Area fill path — replace M/L coordinates with real data.
        Points are: (x, y) in viewBox coords where y=0 is top, y=60 is bottom.
        The path must start at (0, bottom), trace your data, then close back down.
        Example below: 5 data points at x=0,50,100,150,200
      -->
      <path
        class="area-fill"
        d="M0,60 L0,42 L50,28 L100,35 L150,18 L200,24 L200,60 Z"
        fill="url(#area-fill)"
      />

      <!--
        Line stroke path — same x/y data points as the area path above,
        but without the closing Z segment.
      -->
      <path
        class="area-line"
        d="M0,42 L50,28 L100,35 L150,18 L200,24"
        fill="none"
        stroke="#67e8f9"
        stroke-width="1.5"
        stroke-linejoin="round"
        stroke-linecap="round"
      />

      <!-- Data point dots — place one <circle> per data point -->
      <circle cx="0"   cy="42" r="2.5" fill="#67e8f9"/>
      <circle cx="50"  cy="28" r="2.5" fill="#67e8f9"/>
      <circle cx="100" cy="35" r="2.5" fill="#67e8f9"/>
      <circle cx="150" cy="18" r="2.5" fill="#67e8f9"/>
      <circle cx="200" cy="24" r="2.5" fill="#67e8f9"/>
    </svg>
  </div>
</div>
""",
    "css": """
.chart-root {
  background: transparent;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid #243352;
  flex-shrink: 0;
}

.chart-title {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.chart-range {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9px;
  color: #475569;
}

.chart-body {
  display: flex;
  flex: 1;
  min-height: 0;
  padding: 12px;
  gap: 8px;
  align-items: stretch;
}

/* Y-axis tick labels */
.y-axis {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  flex-shrink: 0;
}

.y-axis span {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8px;
  color: #475569;
  line-height: 1;
}

.area-chart {
  flex: 1;
  min-width: 0;
  height: 100%;
  overflow: visible;
}

/* Subtle glow on the line */
.area-line {
  filter: drop-shadow(0 0 3px rgba(103,232,249,0.6));
}
""",
}


# ---------------------------------------------------------------------------
# LOG_STREAM
# A wide live log viewer with a blinking status dot, animated log lines,
# and a subtle scanline overlay for a terminal/CRT aesthetic.
# Replace the log line text content in html to show real log output.
# col_span=2, row_span=1
# ---------------------------------------------------------------------------
LOG_STREAM = {
    "title": "Log Stream",
    "col_span": 2,
    "row_span": 1,
    "description": "Live log viewer with animated line-in effect, colored log prefixes, and scanline overlay",
    "html": """
<div class="log-root">
  <!-- Header bar with blinking LIVE indicator -->
  <div class="log-header">
    <div class="live-indicator">
      <span class="live-dot"></span>
      <span class="live-label">LIVE</span>
    </div>
    <span class="log-source">agent stdout</span>
  </div>

  <!-- Log lines — replace timestamps, levels, and messages with real content -->
  <!-- Supported level classes: .ok .warn .err .info -->
  <div class="log-body">
    <div class="log-line" style="animation-delay: 0ms">
      <span class="ts">[09:14:02]</span>
      <span class="level ok">[OK]</span>
      <span class="msg">Workspace initialized in /tmp/agent-42</span>
    </div>
    <div class="log-line" style="animation-delay: 60ms">
      <span class="ts">[09:14:03]</span>
      <span class="level ok">[OK]</span>
      <span class="msg">Git clone complete — 1,247 files</span>
    </div>
    <div class="log-line" style="animation-delay: 120ms">
      <span class="ts">[09:14:05]</span>
      <span class="level warn">[WARN]</span>
      <span class="msg">No lockfile found, dependency versions may drift</span>
    </div>
    <div class="log-line" style="animation-delay: 180ms">
      <span class="ts">[09:14:07]</span>
      <span class="level ok">[OK]</span>
      <span class="msg">npm install finished — 342 packages</span>
    </div>
    <div class="log-line" style="animation-delay: 240ms">
      <span class="ts">[09:14:09]</span>
      <span class="level err">[ERR]</span>
      <span class="msg">Test suite failed: 3 assertions in auth.spec.ts</span>
    </div>
    <div class="log-line" style="animation-delay: 300ms">
      <span class="ts">[09:14:11]</span>
      <span class="level warn">[WARN]</span>
      <span class="msg">Retrying with --force flag...</span>
    </div>
  </div>

  <!-- Scanline overlay — purely decorative CSS, no content needed here -->
  <div class="scanlines" aria-hidden="true"></div>
</div>
""",
    "css": """
.log-root {
  background: transparent;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  position: relative;
}

/* ---- Header ---- */
.log-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid #243352;
  flex-shrink: 0;
  z-index: 1;
}

.live-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* Blinking green status dot */
.live-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4ade80;
  box-shadow: 0 0 6px rgba(74,222,128,0.8);
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0; }
}

.live-label {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9px;
  color: #4ade80;
  letter-spacing: 0.1em;
}

.log-source {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9px;
  color: #475569;
}

/* ---- Log body ---- */
.log-body {
  flex: 1;
  min-height: 0;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow: hidden;
}

/* Each log line animates in from below */
.log-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px;
  line-height: 1.5;
  animation: slide-up 0.25s ease both;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@keyframes slide-up {
  from { transform: translateY(8px); opacity: 0; }
  to   { transform: translateY(0);   opacity: 1; }
}

.ts {
  color: #334155;
  flex-shrink: 0;
}

.level {
  flex-shrink: 0;
  font-weight: 600;
}

.level.ok   { color: #4ade80; }
.level.warn { color: #fbbf24; }
.level.err  { color: #f87171; }
.level.info { color: #67e8f9; }

.msg {
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ---- Scanline CRT overlay ---- */
.scanlines {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0,0,0,0.03) 2px,
    rgba(0,0,0,0.03) 4px
  );
  z-index: 2;
}
""",
}


# ---------------------------------------------------------------------------
# PROGRESS_RING
# A compact SVG circular progress ring with percentage text centered inside
# and a label below. Use --progress CSS custom property (0.0 to 1.0).
# col_span=1, row_span=1
# ---------------------------------------------------------------------------
PROGRESS_RING = {
    "title": "Progress Ring",
    "col_span": 1,
    "row_span": 1,
    "description": "Circular SVG progress ring with centered percentage, driven by --progress CSS custom property (0.0–1.0)",
    "html": """
<div class="ring-root">
  <!--
    Set --progress to a value between 0.0 and 1.0.
    The stroke-dashoffset formula below converts it to arc length.
    Circle: r=36, circumference = 2*pi*36 ≈ 226.2
    e.g. --progress: 0.72  →  72% filled
  -->
  <div class="ring-wrapper" style="--progress: 0.72">

    <svg class="ring-svg" viewBox="0 0 88 88" aria-hidden="true">
      <!-- Track ring (background) -->
      <circle
        cx="44" cy="44" r="36"
        fill="none"
        stroke="#1a2640"
        stroke-width="4"
      />

      <!--
        Progress arc.
        stroke-dasharray = circumference (226).
        stroke-dashoffset is computed via CSS from --progress.
        The ring starts at 12 o'clock: rotate(-90deg) transform.
      -->
      <circle
        class="ring-arc"
        cx="44" cy="44" r="36"
        fill="none"
        stroke="#4ade80"
        stroke-width="4"
        stroke-linecap="round"
        stroke-dasharray="226"
        transform="rotate(-90 44 44)"
      />
    </svg>

    <!-- Percentage text centered in ring -->
    <div class="ring-pct"></div>

  </div>

  <!-- Label below ring — replace "sprint progress" with your metric name -->
  <div class="ring-label">sprint progress</div>
</div>
""",
    "css": """
.ring-root {
  background: transparent;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 12px;
  box-sizing: border-box;
  gap: 8px;
}

.ring-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* Green glow behind the ring */
  filter: drop-shadow(0 0 6px rgba(74,222,128,0.4));
}

.ring-svg {
  width: 88px;
  height: 88px;
}

/* Drive the progress arc via CSS custom property --progress */
.ring-arc {
  /*
    stroke-dashoffset = circumference * (1 - progress)
    CSS cannot do arithmetic with custom properties natively,
    so we set it inline via a style attribute if needed,
    or use @property (Houdini) if the host supports it.

    Fallback: agents should set the stroke-dashoffset attribute directly
    in the SVG element when calling canvas_put if @property isn't available.

    Using calc() here works in modern browsers when --progress is unitless:
  */
  stroke-dashoffset: calc(226 * (1 - var(--progress, 0.72)));
  transition: stroke-dashoffset 0.6s cubic-bezier(0.22, 1, 0.36, 1);
}

/* Percentage text overlay — uses ::before to display computed value */
.ring-pct {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

/*
  Agents: the simplest way to set the visible percentage is to put
  the number as text content inside .ring-pct in the html string,
  e.g.: <div class="ring-pct">72%</div>
  The styles below will center it.
*/
.ring-pct {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 18px;
  color: #67e8f9;
  font-weight: 500;
  letter-spacing: -0.02em;
}

.ring-label {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9px;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  text-align: center;
}
""",
}


# ---------------------------------------------------------------------------
# ALL_TEMPLATES — import this list to enumerate or serve all templates
# ---------------------------------------------------------------------------
ALL_TEMPLATES = [STAT_COUNTER, SPARKLINE_CHART, LOG_STREAM, PROGRESS_RING]
