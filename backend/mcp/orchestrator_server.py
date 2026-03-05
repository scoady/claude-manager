"""MCP server exposing orchestration tools to controller agents.

This gives the orchestrator explicit, typed tools for managing the
agent lifecycle instead of relying on prompt engineering + Agent tool.

Tools:
  list_tasks      — read current TASKS.md state
  dispatch_agent  — spawn a worker agent for a specific task
  get_agents      — check status of all agents on this project
  report_complete — mark a task done and record a summary
"""
from __future__ import annotations

import os

import httpx
from fastmcp import FastMCP

mcp = FastMCP("orchestrator")

MANAGER_API = os.environ.get("MANAGER_API_URL", "http://localhost:4040")


@mcp.tool()
def list_tasks(project: str) -> list[dict]:
    """
    Get all tasks from TASKS.md for a project.

    Returns a list of tasks with index, text, status (pending/in_progress/done),
    and any subtasks. Use this to understand what work needs to be done.
    """
    url = f"{MANAGER_API}/api/projects/{project}/tasks"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
        resp.raise_for_status()
        tasks = resp.json()
        return [
            {
                "index": i,
                "text": t.get("text", ""),
                "status": t.get("status", "pending"),
                "subtasks": t.get("subtasks", []),
            }
            for i, t in enumerate(tasks)
        ]


@mcp.tool()
def dispatch_agent(
    project: str,
    task_index: int,
    model: str = "",
) -> dict:
    """
    Spawn a worker agent to execute a specific task by its index.

    The manager will:
    1. Mark the task as in_progress in TASKS.md
    2. Spawn a new Claude agent subprocess with the task prompt
    3. The agent works autonomously and reports back when done

    Use list_tasks() first to see available tasks and their indices.
    Returns the session_id of the spawned agent.
    """
    url = f"{MANAGER_API}/api/projects/{project}/tasks/{task_index}/start"
    payload = {}
    if model:
        payload["model"] = model
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload if payload else None)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def get_agents(project: str) -> list[dict]:
    """
    Get the status of all agents working on a project.

    Returns session_id, task, phase (starting/thinking/generating/tool_exec/idle),
    turn_count, and whether each agent is the controller.
    Use this to monitor progress and decide when to dispatch more work.
    """
    url = f"{MANAGER_API}/api/projects/{project}"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data.get("agents", [])


@mcp.tool()
def report_complete(
    project: str,
    task_index: int,
    summary: str,
) -> dict:
    """
    Mark a task as completed and record the outcome.

    Call this after a dispatched agent finishes its work.
    Updates the task checkbox in TASKS.md from [~] to [x].
    The summary is recorded as a project milestone.
    """
    url = f"{MANAGER_API}/api/projects/{project}/tasks/{task_index}/complete"
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, json={"summary": summary})
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def dispatch_custom(
    project: str,
    task: str,
    model: str = "",
) -> dict:
    """
    Dispatch a free-form task (not from TASKS.md) to a new agent.

    Use this for ad-hoc work that doesn't correspond to a numbered task,
    like investigating an issue, running tests, or exploring the codebase.
    Returns the session_id of the spawned agent.
    """
    url = f"{MANAGER_API}/api/projects/{project}/dispatch"
    payload = {"task": task}
    if model:
        payload["model"] = model
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=4042)
