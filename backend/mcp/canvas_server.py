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
    Create or update a widget on the agent canvas for a project.

    The widget renders as an isolated HTML/CSS/JS block in the dashboard.
    Use this to display status, charts, logs, or any custom visualization.

    widget_id: stable ID for this widget — use the same ID on repeated calls to
               update the widget in-place rather than creating a duplicate.
    grid_col/grid_row: 1-indexed position in the canvas grid.
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
    mcp.run(transport="stdio")
