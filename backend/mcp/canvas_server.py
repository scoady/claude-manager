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
    """Render a small status pill badge."""
    color = _status_color(status or label)
    return (
        f'<span style="display:inline-block;font-family:{_FONTS["mono"]};font-size:9px;'
        f"font-weight:500;color:{color};padding:2px 8px;"
        f"background:{color}18;border:1px solid {color}25;"
        f'border-radius:10px;letter-spacing:0.03em">'
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
            f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;'
            f'border-bottom:1px solid {_COLORS["border-dim"]}20">'
            f'<span style="width:5px;height:5px;border-radius:50%;'
            f'background:{_status_color(it.get("status", "pending"))};flex-shrink:0;'
            f'box-shadow:0 0 6px {_status_color(it.get("status", "pending"))}30"></span>'
            f'<span style="font-family:{_FONTS["body"]};font-size:12px;color:{_COLORS["secondary"]};'
            f'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{escape(str(it.get("label", "")))}</span>'
            f'{_badge_html(it.get("status", "pending"))}'
            f"</div>"
            for it in items[:20]
        )
        items_html = f'<div style="margin-top:10px">{rows}</div>'

    details_html = ""
    if details:
        details_html = (
            f'<details style="margin-top:10px">'
            f'<summary style="font-family:{_FONTS["mono"]};font-size:10px;'
            f'color:{_COLORS["cyan"]};cursor:pointer;letter-spacing:0.03em">'
            f"Details</summary>"
            f'<div style="margin-top:8px;padding:10px;'
            f"background:linear-gradient(135deg,{_COLORS['elevated']},{_COLORS['surface']});"
            f'border:1px solid {_COLORS["border-dim"]};border-radius:8px;'
            f"font-family:{_FONTS['mono']};font-size:11px;color:{_COLORS['secondary']};"
            f'white-space:pre-wrap;word-wrap:break-word;line-height:1.5">'
            f"{escape(str(details))}</div></details>"
        )

    html = (
        f'<div style="font-family:{_FONTS["body"]};color:{_COLORS["primary"]}">'
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'
        f"{badge}"
        f'<span style="font-family:{_FONTS["title"]};font-size:14px;font-weight:600;'
        f'color:{_COLORS["primary"]};letter-spacing:-0.01em">{heading}</span>'
        f"</div>"
        f'<div style="font-size:12px;color:{_COLORS["secondary"]};line-height:1.6;'
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
        stats = " ".join(
            f'<span style="color:{_status_color(k)}">{v}</span>'
            f'<span style="color:{_COLORS["muted"]}">{escape(k)}</span>'
            for k, v in breakdown.items()
        )
        stats_html = (
            f'<div style="display:flex;gap:10px;font-family:{_FONTS["mono"]};'
            f'font-size:10px;margin-top:8px">{stats}</div>'
        )

    html = (
        f'<div style="font-family:{_FONTS["body"]};color:{_COLORS["primary"]}">'
        f'<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px">'
        f'<span style="font-family:{_FONTS["title"]};font-size:28px;font-weight:700;'
        f'color:{_COLORS["cyan"]};letter-spacing:-0.03em">{pct}</span>'
        f'<span style="font-family:{_FONTS["mono"]};font-size:11px;color:{_COLORS["muted"]}">%</span>'
        f'<span style="font-family:{_FONTS["body"]};font-size:11px;color:{_COLORS["secondary"]};'
        f'margin-left:auto">{escape(label)}</span>'
        f"</div>"
        f'<div style="height:3px;background:{_COLORS["border-dim"]};border-radius:2px;overflow:hidden">'
        f'<div style="height:100%;width:{pct}%;'
        f"background:linear-gradient(90deg,{_COLORS['green']},{_COLORS['cyan']});"
        f'border-radius:2px;box-shadow:0 0 10px {_COLORS["green"]}40;'
        f'transition:width 0.6s ease"></div></div>'
        f"{stats_html}</div>"
    )
    return html, ""


def _render_key_value(data: dict) -> tuple[str, str]:
    """Key-value pairs display."""
    pairs = data.get("pairs", {})
    rows = "".join(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:6px 0;border-bottom:1px solid {_COLORS["border-dim"]}15">'
        f'<span style="font-family:{_FONTS["body"]};font-size:12px;'
        f'color:{_COLORS["muted"]}">{escape(str(k))}</span>'
        f'<span style="font-family:{_FONTS["mono"]};font-size:12px;'
        f'color:{_COLORS["primary"]}">{escape(str(v))}</span>'
        f"</div>"
        for k, v in pairs.items()
    )
    html = f'<div style="font-family:{_FONTS["body"]};color:{_COLORS["primary"]}">{rows}</div>'
    return html, ""


def _render_log(data: dict) -> tuple[str, str]:
    """Log stream / activity feed."""
    entries = data.get("entries", [])
    rows = "".join(
        f'<div style="display:flex;gap:8px;padding:5px 0;'
        f'border-bottom:1px solid {_COLORS["border-dim"]}12">'
        f'<span style="font-family:{_FONTS["mono"]};font-size:9px;color:{_COLORS["muted"]};'
        f'flex-shrink:0;min-width:50px">{escape(str(e.get("time", "")))}</span>'
        f'{_badge_html(e.get("level", "info"), e.get("level", "info")) if e.get("level") else ""}'
        f'<span style="font-family:{_FONTS["body"]};font-size:11px;color:{_COLORS["secondary"]};'
        f'word-wrap:break-word;overflow-wrap:break-word">{escape(str(e.get("message", "")))}</span>'
        f"</div>"
        for e in entries[:30]
    )
    html = f'<div style="max-height:200px;overflow-y:auto">{rows}</div>'
    return html, ""


_TEMPLATES = {
    "status-card": _render_status_card,
    "progress": _render_progress,
    "key-value": _render_key_value,
    "log": _render_log,
}


# ── MCP tools ────────────────────────────────────────────────────────────────

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

    PREFERRED: Use template + data (JSON string). The server renders
    styled HTML automatically — you never write HTML/CSS.

    Available templates:
      "status-card" — Status badge, heading, description, item list, expandable details.
          data keys: status, heading, description, details (string),
                     items (list of {label, status})
      "progress"    — Big percentage, progress bar, stat breakdown.
          data keys: value, total, label, breakdown (dict of label→count)
      "key-value"   — Clean key-value pairs display.
          data keys: pairs (dict of key→value)
      "log"         — Activity feed / log stream.
          data keys: entries (list of {time, level, message})

    Examples:
      canvas_put(project="myapp", widget_id="build-status", title="Build",
                 template="status-card",
                 data='{"status":"in_progress","heading":"Building v2.1",
                        "description":"Running test suite...",
                        "items":[{"label":"Unit tests","status":"done"},
                                 {"label":"Integration","status":"active"},
                                 {"label":"Deploy","status":"pending"}]}')

      canvas_put(project="myapp", widget_id="task-progress", title="Tasks",
                 template="progress",
                 data='{"value":7,"total":12,"label":"7 of 12 tasks",
                        "breakdown":{"done":7,"active":2,"pending":3}}')

    FALLBACK: Pass raw html/css/js if no template fits your content.

    widget_id: stable ID — reuse the same ID to update in-place.
    col_span/row_span: how many grid cells the widget occupies (default 1).
    """
    rendered_html = html
    rendered_css = css

    if template and data:
        renderer = _TEMPLATES.get(template)
        if renderer:
            parsed = json.loads(data) if isinstance(data, str) else data
            rendered_html, rendered_css = renderer(parsed)

    payload = {
        "id": widget_id,
        "title": title,
        "html": rendered_html,
        "css": rendered_css,
        "js": js,
        "col_span": col_span,
        "row_span": row_span,
    }

    url_put = f"{CANVAS_API}/api/canvas/{project}/widgets/{widget_id}"
    url_post = f"{CANVAS_API}/api/canvas/{project}/widgets"

    with httpx.Client(timeout=10) as client:
        resp = client.put(url_put, json=payload)
        if resp.status_code == 404:
            resp = client.post(url_post, json=payload)
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


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=4041)
