# Widget Templates for Canvas MCP

`widget_templates.py` contains four ready-to-use HTML/CSS widget templates that agents can pass directly into `canvas_put` calls.

## Quick Start

```python
from backend.mcp.widget_templates import STAT_COUNTER, SPARKLINE_CHART, LOG_STREAM, PROGRESS_RING, ALL_TEMPLATES

# Place a stat counter at grid position (0, 0)
await canvas_put(
    widget_id="tasks-done",
    title=STAT_COUNTER["title"],
    html=STAT_COUNTER["html"],
    css=STAT_COUNTER["css"],
    col_span=STAT_COUNTER["col_span"],
    row_span=STAT_COUNTER["row_span"],
)
```

To customise a template, copy the `html` string and modify it before passing it to `canvas_put`. You do **not** need to modify `css` for most changes.

---

## Templates

### STAT_COUNTER (col_span=1, row_span=1)

A metric tile: large centered number, label subtitle, 5-bar sparkline, pulsing glow ring.

**Customisation via CSS custom properties** — edit the `style` attribute on `.stat-inner` in the `html` string:

```python
html = STAT_COUNTER["html"].replace(
    "--value: '47'; --label: 'tasks done'",
    "--value: '128'; --label: 'commits today'",
)
```

The `--value` and `--label` properties are rendered as visible text via CSS `content: var(--value)` on pseudo-elements.

To update the sparkline bars, replace the `height` and `y` attributes on the five `<rect>` elements. Bar height range is 1–20 (viewBox height is 20). `y = 20 - height`.

---

### SPARKLINE_CHART (col_span=2, row_span=1)

A wide area chart with title bar, gradient fill, neon line, data point dots, and Y-axis labels.

**Customisation:**

1. Replace `"CPU Usage"` and `"last 5 min"` in the header.
2. Replace `100 / 50 / 0` in `.y-axis` with your scale values.
3. Replace the `d="..."` attribute on both `<path>` elements with your data points.
   - ViewBox is `0 0 200 60`. X spans 0–200, Y spans 0 (top) to 60 (bottom).
   - Area path: start at `M0,60`, trace your points, close with `L200,60 Z`.
   - Line path: same points without the closing segment.
4. Move the `<circle>` elements to match your data point coordinates.

Example for 5 equal-spaced points at x = 0, 50, 100, 150, 200:

```svg
d="M0,60 L0,30 L50,20 L100,40 L150,15 L200,25 L200,60 Z"
```

---

### LOG_STREAM (col_span=2, row_span=1)

A live log viewer with blinking status dot, 6 animated log lines, and a CRT scanline overlay.

**Customisation:**

Replace the content of each `.log-line` div. Each line has three spans:

```html
<div class="log-line" style="animation-delay: 0ms">
  <span class="ts">[HH:MM:SS]</span>
  <span class="level ok">[OK]</span>
  <span class="msg">Your message here</span>
</div>
```

Supported level classes: `.ok` (green), `.warn` (amber), `.err` (red), `.info` (cyan).

Replace `"agent stdout"` in the header with your log source name.

To add or remove lines, copy/paste a `.log-line` block and increment the `animation-delay` by 60ms per line.

---

### PROGRESS_RING (col_span=1, row_span=1)

A circular SVG progress ring with centered percentage text and a label below.

**Customisation via CSS custom property `--progress`** — edit the `style` attribute on `.ring-wrapper`:

```python
html = PROGRESS_RING["html"].replace(
    "--progress: 0.72",
    "--progress: 0.45",
)
```

Also replace the percentage text content and label:

```python
html = html.replace(
    '<div class="ring-pct"></div>',
    '<div class="ring-pct">45%</div>',
).replace(
    'sprint progress',
    'test coverage',
)
```

Note: `stroke-dashoffset: calc(226 * (1 - var(--progress)))` works in all modern browsers. If you need to support an older WebView, set the `stroke-dashoffset` attribute directly on the `<circle class="ring-arc">` element:

```python
offset = 226 * (1 - 0.45)  # = 124.3
html = html.replace('stroke-dasharray="226"', f'stroke-dasharray="226" stroke-dashoffset="{offset:.1f}"')
```

---

## Design Constraints (all templates)

- `background: transparent` — the WidgetFrame card provides the dark background.
- No external fetches — all SVG is inline; fonts are loaded by the host page.
- Shadow DOM safe — class names are scoped, no globals.
- Padding: 12px inside the content area.
- Animations use `animation-delay` for staggered reveals and `transition` for value changes.

---

## Enumerating All Templates

```python
from backend.mcp.widget_templates import ALL_TEMPLATES

for t in ALL_TEMPLATES:
    print(t["title"], t["col_span"], "x", t["row_span"], "—", t["description"])
```

Output:
```
Stat Counter 1 x 1 — A metric tile showing a large number with label and trend indicator
Sparkline Chart 2 x 1 — Wide area chart with gradient fill, neon line stroke, and Y-axis tick labels
Log Stream 2 x 1 — Live log viewer with animated line-in effect, colored log prefixes, and scanline overlay
Progress Ring 1 x 1 — Circular SVG progress ring with centered percentage, driven by --progress CSS custom property (0.0–1.0)
```
