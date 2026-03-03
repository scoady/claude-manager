"""
ToolExecutor — implements the standard tool set for managed agents.

Tools run as asyncio coroutines (file I/O via executor, Bash via subprocess).
Each ToolExecutor can be scoped to an allow/deny list per project.
"""
from __future__ import annotations

import asyncio
import glob as glob_mod
import os
import re
from pathlib import Path
from typing import Any

# ── Tool schemas in Anthropic format ─────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "Bash",
        "description": "Run a bash command. Working directory is the project root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "description": {"type": "string", "description": "What this command does."},
                "timeout": {"type": "integer", "description": "Timeout in ms (default 120000)."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Read",
        "description": "Read a file. Returns numbered lines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer", "description": "Line offset (0-based)."},
                "limit": {"type": "integer", "description": "Max lines to return (default 2000)."},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Write",
        "description": "Write content to a file, creating parent directories as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": "Replace an exact string in a file. old_string must be unique.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Glob",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
                "path": {"type": "string", "description": "Base directory (default: project root)."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Search file contents with a regular expression.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern."},
                "path": {"type": "string", "description": "Directory to search (default: project root)."},
                "glob": {"type": "string", "description": "File glob filter, e.g. *.py"},
            },
            "required": ["pattern"],
        },
    },
]


class ToolExecutor:
    def __init__(
        self,
        allowed_tools: list[str] | None = None,
        denied_tools: list[str] | None = None,
    ) -> None:
        self._allowed = set(allowed_tools) if allowed_tools is not None else None
        self._denied = set(denied_tools or [])

    def tool_schemas(self) -> list[dict[str, Any]]:
        schemas = TOOL_SCHEMAS
        if self._allowed is not None:
            schemas = [s for s in schemas if s["name"] in self._allowed]
        return [s for s in schemas if s["name"] not in self._denied]

    async def execute(self, tool_name: str, tool_input: dict[str, Any], cwd: str) -> str:
        if self._allowed is not None and tool_name not in self._allowed:
            return f"Error: tool '{tool_name}' is not allowed."
        if tool_name in self._denied:
            return f"Error: tool '{tool_name}' is denied."

        handlers = {
            "Bash":  self._bash,
            "Read":  self._read,
            "Write": self._write,
            "Edit":  self._edit,
            "Glob":  self._glob,
            "Grep":  self._grep,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return f"Error: unknown tool '{tool_name}'"
        return await handler(tool_input, cwd)

    # ── Bash ──────────────────────────────────────────────────────────────────

    async def _bash(self, inp: dict[str, Any], cwd: str) -> str:
        command = inp.get("command", "")
        timeout_s = min(inp.get("timeout", 120_000) / 1000, 600)

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd if Path(cwd).is_dir() else None,
            env={**os.environ},
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"Error: command timed out after {timeout_s:.0f}s"

        out = stdout.decode("utf-8", errors="replace")
        if len(out) > 50_000:
            out = out[:25_000] + "\n...[truncated]...\n" + out[-25_000:]
        return out or f"(exit {proc.returncode})"

    # ── Read ──────────────────────────────────────────────────────────────────

    async def _read(self, inp: dict[str, Any], cwd: str) -> str:
        path = self._resolve(inp["file_path"], cwd)
        offset = inp.get("offset", 0)
        limit = inp.get("limit", 2000)
        loop = asyncio.get_event_loop()
        try:
            text = await loop.run_in_executor(None, path.read_text, "utf-8")
        except FileNotFoundError:
            return f"Error: file not found: {path}"
        except Exception as exc:
            return f"Error reading {path}: {exc}"
        lines = text.splitlines()
        subset = lines[offset: offset + limit]
        return "\n".join(f"{i + offset + 1}\t{line}" for i, line in enumerate(subset))

    # ── Write ─────────────────────────────────────────────────────────────────

    async def _write(self, inp: dict[str, Any], cwd: str) -> str:
        path = self._resolve(inp["file_path"], cwd)
        content = inp.get("content", "")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: (
                path.parent.mkdir(parents=True, exist_ok=True),
                path.write_text(content, "utf-8"),
            ))
        except Exception as exc:
            return f"Error writing {path}: {exc}"
        return f"Written {len(content)} bytes to {path}"

    # ── Edit ──────────────────────────────────────────────────────────────────

    async def _edit(self, inp: dict[str, Any], cwd: str) -> str:
        path = self._resolve(inp["file_path"], cwd)
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        loop = asyncio.get_event_loop()
        try:
            text = await loop.run_in_executor(None, path.read_text, "utf-8")
        except FileNotFoundError:
            return f"Error: file not found: {path}"
        if old not in text:
            return f"Error: old_string not found in {path}"
        updated = text.replace(old, new, 1)
        try:
            await loop.run_in_executor(None, path.write_text, updated, "utf-8")
        except Exception as exc:
            return f"Error writing {path}: {exc}"
        return f"Edited {path}"

    # ── Glob ──────────────────────────────────────────────────────────────────

    async def _glob(self, inp: dict[str, Any], cwd: str) -> str:
        pattern = inp.get("pattern", "*")
        base = inp.get("path", cwd)
        if not Path(base).is_absolute():
            base = str(Path(cwd) / base)
        full_pattern = str(Path(base) / pattern)
        loop = asyncio.get_event_loop()
        matches = await loop.run_in_executor(
            None, lambda: sorted(glob_mod.glob(full_pattern, recursive=True))
        )
        return "\n".join(matches[:500]) if matches else "(no matches)"

    # ── Grep ──────────────────────────────────────────────────────────────────

    async def _grep(self, inp: dict[str, Any], cwd: str) -> str:
        pattern = inp.get("pattern", "")
        search_path = inp.get("path", cwd)
        if not Path(search_path).is_absolute():
            search_path = str(Path(cwd) / search_path)
        file_glob = inp.get("glob", "")

        # Try ripgrep first, fall back to Python
        cmd = ["rg", "--line-number", "--no-heading", "-m", "200", pattern]
        if file_glob:
            cmd += ["--glob", file_glob]
        cmd.append(search_path)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            return stdout.decode("utf-8", errors="replace") or "(no matches)"
        except (FileNotFoundError, asyncio.TimeoutError):
            return await self._grep_python(pattern, search_path, file_glob)

    async def _grep_python(self, pattern: str, search_path: str, file_glob: str) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        glob_pattern = str(Path(search_path) / (file_glob or "**/*"))
        loop = asyncio.get_event_loop()
        files = await loop.run_in_executor(
            None, lambda: glob_mod.glob(glob_pattern, recursive=True)
        )
        results: list[str] = []
        for fp in files[:200]:
            try:
                text = Path(fp).read_text("utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        results.append(f"{fp}:{i}:{line}")
                        if len(results) >= 200:
                            break
            except Exception:
                pass
        return "\n".join(results) or "(no matches)"

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve(file_path: str, cwd: str) -> Path:
        p = Path(file_path)
        return p if p.is_absolute() else Path(cwd) / p
