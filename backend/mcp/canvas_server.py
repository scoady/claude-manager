"""MCP server exposing canvas tools to Claude agents."""
from __future__ import annotations

import os

import httpx
from fastmcp import FastMCP

mcp = FastMCP("canvas")

# Allow override via env so the server works both locally and inside docker-compose
CANVAS_API = os.environ.get("CANVAS_API_URL", "http://localhost:4040")


@mcp.tool()
def canvas_put(
    project: str,
    widget_id: str,
    title: str,
    html: str,
    css: str = "",
    js: str = "",
    grid_col: int = 1,
    grid_row: int = 1,
    col_span: int = 1,
    row_span: int = 1,
) -> dict:
    """
    Create or update a dashboard widget for a project.

    Widgets appear directly on the project's Overview dashboard. Use this to
    publish task status, progress summaries, architecture diagrams, code
    highlights, or any structured project information.

    REQUIRED CONTENT: Every widget MUST include:
    - A clear TASK or TOPIC name in the title
    - A STATUS indicator (e.g. in-progress, done, blocked, planned)
    - A concise summary visible at a glance
    - A <details> element containing the FULL TEXT (detailed explanation,
      code snippets, logs, etc.) that users can expand on click

    STYLING GUIDELINES:
    - Use self-contained inline HTML + CSS (no external dependencies)
    - Colors: cyan (#67e8f9), green (#4ade80), amber (#fbbf24), purple (#a78bfa)
    - Fonts: 'IBM Plex Mono' for data, system-ui for body text
    - Transparent backgrounds (the widget frame provides the card bg)
    - Status badges: green=done, amber=in-progress, cyan=planned, red=blocked

    TEXT FITTING (CRITICAL):
    - ALL text MUST fit within the widget bounds — never overflow horizontally
    - Use word-wrap:break-word and overflow-wrap:break-word on all text containers
    - Long strings (paths, URLs, hashes): use text-overflow:ellipsis with overflow:hidden
    - Tables: use table-layout:fixed with percentage widths; td cells need overflow:hidden
    - Pre/code blocks: use white-space:pre-wrap to wrap long lines
    - Test assumption: widgets are ~300-400px wide. Design content to fit that width.
    - Prefer concise labels and values; truncate with "…" rather than let text overflow

    widget_id: stable ID — use the same ID on repeated calls to update in-place.
    grid_col/grid_row: 1-indexed position in the dashboard grid.
    col_span/row_span: how many grid cells the widget occupies (default 1).
    """
    payload = {
        "id": widget_id,
        "title": title,
        "html": html,
        "css": css,
        "js": js,
        "grid_col": grid_col,
        "grid_row": grid_row,
        "col_span": col_span,
        "row_span": row_span,
    }

    url_put = f"{CANVAS_API}/api/canvas/{project}/widgets/{widget_id}"
    url_post = f"{CANVAS_API}/api/canvas/{project}/widgets"

    with httpx.Client(timeout=10) as client:
        # Try PUT (update existing)
        resp = client.put(url_put, json=payload)
        if resp.status_code == 404:
            # Widget does not exist yet — POST to create, passing widget_id so
            # the backend uses it as the widget's ID rather than generating a
            # new UUID.  This ensures the caller's widget_id is preserved.
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
        # Return a concise summary — omit large html/css/js blobs
        return [
            {
                "id": w["id"],
                "title": w.get("title", ""),
                "grid_col": w.get("grid_col", 1),
                "grid_row": w.get("grid_row", 1),
                "col_span": w.get("col_span", 1),
                "row_span": w.get("row_span", 1),
                "created_at": w.get("created_at"),
                "updated_at": w.get("updated_at"),
            }
            for w in widgets
        ]


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=4041)
