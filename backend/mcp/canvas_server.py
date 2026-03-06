"""MCP server exposing canvas tools to Claude agents.

Agents pass structured data; this server renders it into styled HTML
using the app's design system. No agent ever writes raw HTML/CSS.
"""
from __future__ import annotations

import json
import os
from html import escape

import httpx
from fastmcp import FastMCP

mcp = FastMCP("canvas")

CANVAS_API = os.environ.get("CANVAS_API_URL", "http://localhost:4040")


# ── Design tokens ────────────────────────────────────────────────────────────

_FONTS = {
    "title": "'Plus Jakarta Sans', system-ui, sans-serif",
    "body": "'DM Sans', system-ui, sans-serif",
    "mono": "'IBM Plex Mono', 'Fira Code', monospace",
}
_COLORS = {
    "primary": "#e2e8f0",
    "secondary": "#94a3b8",
    "muted": "#475569",
    "surface": "#0e1525",
    "elevated": "#141d30",
    "border": "#243352",
    "border-dim": "#1a2640",
    "cyan": "#67e8f9",
    "green": "#4ade80",
    "amber": "#fbbf24",
    "red": "#f87171",
    "purple": "#a78bfa",
    "magenta": "#c084fc",
    "teal": "#5eead4",
    "pink": "#f9a8d4",
    "blue": "#60a5fa",
}
_STATUS_COLORS = {
    "done": _COLORS["green"],
    "complete": _COLORS["green"],
    "success": _COLORS["green"],
    "active": _COLORS["amber"],
    "in_progress": _COLORS["amber"],
    "in-progress": _COLORS["amber"],
    "working": _COLORS["amber"],
    "running": _COLORS["amber"],
    "pending": _COLORS["muted"],
    "planned": _COLORS["cyan"],
    "blocked": _COLORS["red"],
    "error": _COLORS["red"],
    "failed": _COLORS["red"],
    "ready": _COLORS["cyan"],
    "idle": _COLORS["secondary"],
}


def _status_color(status: str) -> str:
    return _STATUS_COLORS.get(status.lower().strip(), _COLORS["secondary"])


def _badge_html(label: str, status: str = "") -> str:
    """Render a small status pill badge — matches project-tile-agents style."""
    color = _status_color(status or label)
    return (
        f'<span style="display:inline-block;font-family:{_FONTS["mono"]};font-size:9px;'
        f"font-weight:500;color:{color};padding:2px 7px;"
        f"background:{color}14;border:1px solid {color}20;"
        f'border-radius:8px;letter-spacing:0.03em;flex-shrink:0">'
        f"{escape(label.upper())}</span>"
    )


# ── Template renderers ───────────────────────────────────────────────────────

def _render_status_card(data: dict) -> tuple[str, str]:
    """Status card: status badge, heading, description, optional details."""
    status = data.get("status", "")
    heading = escape(data.get("heading", ""))
    description = escape(data.get("description", ""))
    details = data.get("details", "")
    items = data.get("items", [])

    badge = _badge_html(status, status) if status else ""
    items_html = ""
    if items:
        rows = "".join(
            f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;'
            f'border-bottom:1px solid rgba(26,38,64,0.3)">'
            f'<span style="width:6px;height:6px;border-radius:50%;'
            f'background:{_status_color(it.get("status", "pending"))};flex-shrink:0;'
            f'box-shadow:0 0 8px {_status_color(it.get("status", "pending"))}30;'
            f'transition:all 0.3s ease"></span>'
            f'<span style="font-size:11px;color:{_COLORS["secondary"]};'
            f'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{escape(str(it.get("label", "")))}</span>'
            f'{_badge_html(it.get("status", "pending"))}'
            f"</div>"
            for it in items[:20]
        )
        items_html = f'<div style="margin-top:10px;display:flex;flex-direction:column;gap:1px">{rows}</div>'

    details_html = ""
    if details:
        details_html = (
            f'<details style="margin-top:10px">'
            f'<summary style="font-family:{_FONTS["mono"]};font-size:10px;'
            f'color:{_COLORS["cyan"]};cursor:pointer;letter-spacing:0.03em;'
            f'transition:color 0.2s ease">'
            f"Details</summary>"
            f'<div style="margin-top:8px;padding:10px;'
            f"background:linear-gradient(135deg,{_COLORS['elevated']},{_COLORS['surface']});"
            f'border:1px solid {_COLORS["border-dim"]};border-radius:8px;'
            f"font-family:{_FONTS['mono']};font-size:11px;color:{_COLORS['secondary']};"
            f'white-space:pre-wrap;word-wrap:break-word;line-height:1.5">'
            f"{escape(str(details))}</div></details>"
        )

    html = (
        f'<div>'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
        f"{badge}"
        f'<span style="font-family:{_FONTS["title"]};font-size:14px;font-weight:700;'
        f'color:{_COLORS["primary"]};letter-spacing:-0.01em">{heading}</span>'
        f"</div>"
        f'<div style="font-size:11px;color:{_COLORS["secondary"]};line-height:1.6;'
        f'word-wrap:break-word">{description}</div>'
        f"{items_html}{details_html}</div>"
    )
    return html, ""


def _render_progress(data: dict) -> tuple[str, str]:
    """Progress widget: big number, progress bar, stat breakdown."""
    value = data.get("value", 0)
    total = data.get("total", 100)
    label = escape(data.get("label", "Progress"))
    pct = round((value / total) * 100) if total > 0 else 0
    breakdown = data.get("breakdown", {})

    stats_html = ""
    if breakdown:
        stat_items = []
        for k, v in breakdown.items():
            color = _status_color(k)
            stat_items.append(
                f'<span style="display:inline-flex;align-items:center;gap:4px">'
                f'<span style="font-weight:500;color:{color}">{v}</span>'
                f'<span style="color:{_COLORS["muted"]}">{escape(k)}</span></span>'
            )
        sep = f'<span style="color:{_COLORS["border"]}">·</span>'
        stats_html = (
            f'<div style="display:flex;gap:8px;font-family:{_FONTS["mono"]};'
            f'font-size:10px;margin-top:10px;align-items:center">'
            f"{sep.join(stat_items)}</div>"
        )

    html = (
        f'<div>'
        f'<div style="display:flex;align-items:baseline;gap:4px;margin-bottom:10px">'
        f'<span style="font-family:{_FONTS["title"]};font-size:28px;font-weight:800;'
        f'color:{_COLORS["cyan"]};letter-spacing:-0.03em">{pct}</span>'
        f'<span style="font-family:{_FONTS["mono"]};font-size:11px;color:{_COLORS["muted"]}">%</span>'
        f'<span style="font-size:11px;color:{_COLORS["secondary"]};'
        f'margin-left:auto">{escape(label)}</span>'
        f"</div>"
        f'<div style="height:3px;background:{_COLORS["border-dim"]};border-radius:2px;overflow:hidden">'
        f'<div style="height:100%;width:{pct}%;'
        f"background:linear-gradient(90deg,{_COLORS['green']},{_COLORS['cyan']});"
        f'border-radius:2px;box-shadow:0 0 12px {_COLORS["green"]}40;'
        f'transition:width 0.6s cubic-bezier(0.4,0,0.2,1)"></div></div>'
        f"{stats_html}</div>"
    )
    return html, ""


def _render_key_value(data: dict) -> tuple[str, str]:
    """Key-value pairs display."""
    pairs = data.get("pairs", {})
    rows = "".join(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:6px 0;border-bottom:1px solid rgba(26,38,64,0.25)">'
        f'<span style="font-size:11px;'
        f'color:{_COLORS["muted"]}">{escape(str(k))}</span>'
        f'<span style="font-family:{_FONTS["mono"]};font-size:11px;'
        f'color:{_COLORS["primary"]};font-weight:500">{escape(str(v))}</span>'
        f"</div>"
        for k, v in pairs.items()
    )
    html = f'<div style="display:flex;flex-direction:column;gap:1px">{rows}</div>'
    return html, ""


def _render_log(data: dict) -> tuple[str, str]:
    """Log stream / activity feed."""
    entries = data.get("entries", [])
    rows = "".join(
        f'<div style="display:flex;gap:8px;padding:4px 0;'
        f'border-bottom:1px solid rgba(26,38,64,0.2)">'
        f'<span style="font-family:{_FONTS["mono"]};font-size:9px;color:{_COLORS["muted"]};'
        f'flex-shrink:0;min-width:50px">{escape(str(e.get("time", "")))}</span>'
        f'{_badge_html(e.get("level", "info"), e.get("level", "info")) if e.get("level") else ""}'
        f'<span style="font-size:11px;color:{_COLORS["secondary"]};'
        f'flex:1;word-wrap:break-word;overflow-wrap:break-word">{escape(str(e.get("message", "")))}</span>'
        f"</div>"
        for e in entries[:30]
    )
    html = f'<div style="display:flex;flex-direction:column;gap:1px;overflow-y:auto">{rows}</div>'
    return html, ""


_TEMPLATES = {
    "status-card": _render_status_card,
    "progress": _render_progress,
    "key-value": _render_key_value,
    "log": _render_log,
}


# ── MCP tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def canvas_capabilities() -> dict:
    """
    Return a comprehensive capability manifest describing everything the
    canvas rendering environment supports.

    Call this FIRST before creating widgets to understand:
    - The rendering model (how widget JS code is executed)
    - Available CDN libraries you can load (Three.js, D3, GSAP, etc.)
    - Helper patterns for loading scripts and CSS
    - Design tokens (colors, fonts, glass effects)
    - Cross-widget communication patterns
    - Example code snippets for common visualizations

    This is your reference guide for building rich, animated, interactive
    dashboard widgets.
    """
    return {
        "rendering_model": {
            "execution": "new Function('root','host', code) -- bare function body, NOT an IIFE",
            "container": "root is the widget DOM element; host is the GridStack item wrapper",
            "grid": "12-column GridStack, cellHeight=55px, margin=4, float=true",
            "lifecycle": "Code runs once on mount. Use requestAnimationFrame for animation loops. Clean up with root.cleanup = () => { ... } which is called on widget removal.",
        },
        "cdn_libraries": [
            {
                "name": "three",
                "url": "https://cdn.jsdelivr.net/npm/three@0.160/build/three.module.min.js",
                "type": "esm",
                "use_cases": ["3D visualization", "particle systems", "starfields"],
                "load_pattern": "const THREE = await import('https://cdn.jsdelivr.net/npm/three@0.160/build/three.module.min.js');",
            },
            {
                "name": "d3",
                "url": "https://cdn.jsdelivr.net/npm/d3@7/+esm",
                "type": "esm",
                "use_cases": ["charts", "force graphs", "hierarchies", "maps"],
                "load_pattern": "const d3 = await import('https://cdn.jsdelivr.net/npm/d3@7/+esm');",
            },
            {
                "name": "gsap",
                "url": "https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js",
                "type": "script",
                "use_cases": ["animation", "tweens", "timelines", "scroll effects"],
            },
            {
                "name": "xterm",
                "url": "https://cdn.jsdelivr.net/npm/xterm@5/lib/xterm.min.js",
                "css": "https://cdn.jsdelivr.net/npm/xterm@5/css/xterm.min.css",
                "type": "script",
                "use_cases": ["terminal emulation", "log viewers"],
            },
            {
                "name": "cytoscape",
                "url": "https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js",
                "type": "script",
                "use_cases": ["network graphs", "dependency trees", "relationship maps"],
            },
            {
                "name": "p5",
                "url": "https://cdn.jsdelivr.net/npm/p5@1/lib/p5.min.js",
                "type": "script",
                "use_cases": ["creative coding", "generative art", "interactive sketches"],
            },
            {
                "name": "anime",
                "url": "https://cdn.jsdelivr.net/npm/animejs@4/lib/anime.min.js",
                "type": "script",
                "use_cases": ["DOM animation", "SVG morphing", "staggered reveals"],
            },
            {
                "name": "chart.js",
                "url": "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js",
                "type": "script",
                "use_cases": ["bar charts", "line charts", "radar", "doughnut"],
            },
            {
                "name": "highlight.js",
                "url": "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11/highlight.min.js",
                "css": "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11/styles/github-dark.min.css",
                "type": "script",
                "use_cases": ["code syntax highlighting"],
            },
        ],
        "cdn_loader_pattern": (
            "function loadCDN(name, url, type) { "
            "if (type==='esm') return import(url); "
            "return new Promise((resolve) => { "
            "if (window[name]) return resolve(window[name]); "
            "const s = document.createElement('script'); "
            "s.src = url; s.onload = () => resolve(window[name]); "
            "document.head.appendChild(s); }); }"
        ),
        "css_loader_pattern": (
            "function loadCSS(url) { "
            "if (document.querySelector('link[href=\"'+url+'\"]')) return; "
            "const l = document.createElement('link'); l.rel='stylesheet'; "
            "l.href=url; document.head.appendChild(l); }"
        ),
        "templates": list(_TEMPLATES.keys()),
        "cross_widget_comms": {
            "pattern": "window.__canvasBuffer[widgetId] = data; // write from any widget, read from any other",
            "events": "root.dispatchEvent(new CustomEvent('widget-data', {detail, bubbles:true}))",
        },
        "design_tokens": {
            "colors": {
                "cyan": _COLORS["cyan"],
                "amber": _COLORS["amber"],
                "purple": _COLORS["purple"],
                "green": _COLORS["green"],
                "red": _COLORS["red"],
                "blue": _COLORS["blue"],
                "teal": _COLORS["teal"],
                "pink": _COLORS["pink"],
                "magenta": _COLORS["magenta"],
                "primary": _COLORS["primary"],
                "secondary": _COLORS["secondary"],
                "muted": _COLORS["muted"],
                "surface": _COLORS["surface"],
                "elevated": _COLORS["elevated"],
                "border": _COLORS["border"],
            },
            "glass": (
                "background: rgba(255,255,255,.04); "
                "border: 1px solid rgba(255,255,255,.08); "
                "backdrop-filter: blur(24px)"
            ),
            "mono_font": _FONTS["mono"],
            "title_font": _FONTS["title"],
            "body_font": _FONTS["body"],
            "bg": "#030509",
        },
        "example_snippets": {
            "canvas_2d_particle_system": (
                "const c = document.createElement('canvas'); "
                "root.appendChild(c); const ctx = c.getContext('2d'); "
                "c.width = root.offsetWidth; c.height = root.offsetHeight; "
                "const particles = Array.from({length:80}, () => ({x:Math.random()*c.width, "
                "y:Math.random()*c.height, vx:(Math.random()-0.5)*0.5, vy:(Math.random()-0.5)*0.5, "
                "r:Math.random()*2+1})); "
                "function tick() { ctx.clearRect(0,0,c.width,c.height); "
                "particles.forEach(p => { p.x+=p.vx; p.y+=p.vy; "
                "if(p.x<0||p.x>c.width) p.vx*=-1; if(p.y<0||p.y>c.height) p.vy*=-1; "
                "ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2); "
                "ctx.fillStyle='#67e8f9'; ctx.fill(); }); "
                "requestAnimationFrame(tick); } tick();"
            ),
            "d3_force_graph": (
                "import('https://cdn.jsdelivr.net/npm/d3@7/+esm').then(d3 => { "
                "const w=root.offsetWidth, h=root.offsetHeight; "
                "const svg=d3.select(root).append('svg').attr('width',w).attr('height',h); "
                "const nodes=[{id:'A'},{id:'B'},{id:'C'}]; "
                "const links=[{source:'A',target:'B'},{source:'B',target:'C'}]; "
                "const sim=d3.forceSimulation(nodes)"
                ".force('link',d3.forceLink(links).id(d=>d.id).distance(60))"
                ".force('charge',d3.forceManyBody().strength(-100))"
                ".force('center',d3.forceCenter(w/2,h/2)); "
                "const link=svg.selectAll('line').data(links).join('line')"
                ".attr('stroke','#243352').attr('stroke-width',1); "
                "const node=svg.selectAll('circle').data(nodes).join('circle')"
                ".attr('r',6).attr('fill','#67e8f9'); "
                "sim.on('tick',()=>{ link.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y)"
                ".attr('x2',d=>d.target.x).attr('y2',d=>d.target.y); "
                "node.attr('cx',d=>d.x).attr('cy',d=>d.y); }); });"
            ),
            "fetch_project_data": (
                "const proj = host.closest('[data-active-project]')"
                "?.dataset?.activeProject || 'unknown'; "
                "fetch('/api/projects/'+proj+'/tasks').then(r=>r.json())"
                ".then(tasks => { /* render tasks here */ });"
            ),
        },
    }


@mcp.tool()
def canvas_put(
    project: str,
    widget_id: str,
    title: str,
    template: str = "",
    data: str = "",
    html: str = "",
    css: str = "",
    js: str = "",
    col_span: int = 1,
    row_span: int = 1,
) -> dict:
    """
    Create or update a dashboard widget for a project.

    PREFERRED: Use template + data (JSON string). The backend renders
    styled HTML automatically from the widget catalog -- you never write HTML/CSS.
    Pass a template ID from the widget catalog and a JSON data string matching
    the template's data schema. The backend stores template_id + data so
    widgets can be re-rendered with fresh data on updates.

    FALLBACK: Pass raw html/css/js if no template fits your content.
    For the js field, write a BARE FUNCTION BODY (not an IIFE). It receives
    (root, host) where root is the widget DOM element. You can load CDN
    libraries dynamically -- call canvas_capabilities() first to see the full
    list of available libraries, design tokens, and code patterns.

    widget_id: stable ID -- reuse the same ID to update in-place.
    col_span/row_span: how many grid cells the widget occupies (default 1).
    """
    parsed_data = None
    if data:
        parsed_data = json.loads(data) if isinstance(data, str) else data

    # If using a template, let the backend canvas service handle rendering.
    # Pass template_id + template_data; the backend auto-renders from catalog.
    # Also support legacy inline templates as a fallback.
    rendered_html = html
    rendered_css = css

    if template and parsed_data:
        renderer = _TEMPLATES.get(template)
        if renderer:
            # Legacy inline template — render here
            rendered_html, rendered_css = renderer(parsed_data)

    payload = {
        "id": widget_id,
        "title": title,
        "html": rendered_html,
        "css": rendered_css,
        "js": js,
        "col_span": col_span,
        "row_span": row_span,
    }

    # Pass template_id + template_data for catalog templates
    # (the backend canvas service auto-renders and stores them)
    if template and parsed_data and template not in _TEMPLATES:
        payload["template_id"] = template
        payload["template_data"] = parsed_data

    url_put = f"{CANVAS_API}/api/canvas/{project}/widgets/{widget_id}"
    url_post = f"{CANVAS_API}/api/canvas/{project}/widgets"

    with httpx.Client(timeout=10) as client:
        resp = client.put(url_put, json=payload)
        if resp.status_code == 404:
            resp = client.post(url_post, json=payload)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def canvas_design(
    project: str,
    widget_id: str,
    intent: str,
    data: str = "{}",
) -> dict:
    """
    Design a custom widget -- describe what you want, get a polished result.

    Instead of writing HTML yourself, describe your VISION and pass your DATA.
    A design agent will create a beautiful, animated widget using the app's
    full frontend stack (SVG, canvas, CSS animations, particles, etc).

    The rendering environment supports CDN library loading (Three.js, D3, GSAP,
    Chart.js, Cytoscape, p5.js, etc.) and uses a space/constellation design
    theme with frosted glass, neon glows, and particle effects. Call
    canvas_capabilities() to see the full library list and design tokens.

    intent: Describe what you want visually. Be specific and creative.
        Examples:
        - "constellation map showing task dependencies with glowing active nodes"
        - "animated particle field where each particle represents a completed task"
        - "radial progress ring with orbiting status indicators"
        - "live activity heatmap grid showing agent work intensity"
        - "flowing gradient mesh that shifts color based on project health"

    data: JSON string with the structured data to visualize.
        Example: '{"tasks": [...], "agents": [...], "progress": 0.72}'

    widget_id: stable ID for updates. Reuse to refresh the same widget.

    This is SLOWER than canvas_put templates (~5-10s) but produces unique,
    creative visualizations. Use canvas_put templates for quick status updates,
    and canvas_design for impressive custom visuals.
    """
    payload = {
        "widget_id": widget_id,
        "intent": intent,
        "data": json.loads(data) if isinstance(data, str) else data,
    }

    url = f"{CANVAS_API}/api/canvas/{project}/design"
    with httpx.Client(timeout=60) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def canvas_remove(project: str, widget_id: str) -> dict:
    """Remove a widget from the canvas by ID."""
    url = f"{CANVAS_API}/api/canvas/{project}/widgets/{widget_id}"
    with httpx.Client(timeout=10) as client:
        resp = client.delete(url)
        if resp.status_code == 404:
            return {"ok": False, "error": "Widget not found"}
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def canvas_list(project: str) -> list:
    """
    List all widgets currently on the canvas for a project.

    Returns id, title, grid position, and timestamps.
    Use this to understand current canvas state before making changes.
    """
    url = f"{CANVAS_API}/api/canvas/{project}"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
        resp.raise_for_status()
        widgets = resp.json()
        return [
            {
                "id": w["id"],
                "title": w.get("title", ""),
                "col_span": w.get("col_span", 1),
                "row_span": w.get("row_span", 1),
                "created_at": w.get("created_at"),
                "updated_at": w.get("updated_at"),
            }
            for w in widgets
        ]


@mcp.tool()
def canvas_templates() -> list[dict]:
    """
    List available widget templates from the catalog.

    Returns template IDs, names, categories, and data schemas.
    Use the template ID in canvas_put(template=..., data=...) to create widgets.
    The backend renders the template — you just push data matching the schema.
    """
    url = f"{CANVAS_API}/api/widget-catalog"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
        resp.raise_for_status()
        templates = resp.json()
    return [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "category": t.get("category"),
            "description": t.get("description"),
            "data_schema": t.get("data_schema", {}),
        }
        for t in templates
    ]


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=4041)
