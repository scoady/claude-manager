"""Artifacts service — file browsing and content preview for managed projects."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .projects import MANAGED_DIR

# Max file size for content preview (500KB)
_MAX_FILE_SIZE = 500 * 1024

# Binary/unreadable extensions to skip
_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".exe", ".dll", ".so", ".dylib", ".o",
    ".pyc", ".pyo", ".class", ".jar",
    ".db", ".sqlite", ".sqlite3",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
}


def _project_root(project_name: str) -> Path:
    root = (MANAGED_DIR / project_name).resolve()
    if not root.exists():
        raise ValueError(f"Project '{project_name}' not found")
    return root


def _safe_path(project_name: str, rel_path: str) -> Path:
    """Resolve a relative path within a project, preventing traversal."""
    root = _project_root(project_name)
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("Path traversal detected")
    return target


def list_files(project_name: str, path: str = "") -> list[dict]:
    """List directory contents with name, type, size, mtime."""
    target = _safe_path(project_name, path)
    if not target.is_dir():
        raise ValueError(f"Not a directory: {path}")

    # Get gitignore patterns
    ignored = _get_gitignored_files(project_name, target)

    entries = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            name = item.name
            # Skip hidden files/dirs and common noise
            if name.startswith(".") and name not in (".claude",):
                continue
            if name in ("node_modules", "__pycache__", ".git", ".worktrees", ".workspaces"):
                continue
            if str(item) in ignored:
                continue

            try:
                stat = item.stat()
            except OSError:
                continue

            entries.append({
                "name": name,
                "type": "directory" if item.is_dir() else "file",
                "size": stat.st_size if item.is_file() else None,
                "mtime": stat.st_mtime,
                "path": str(item.relative_to(_project_root(project_name))),
            })
    except PermissionError:
        pass

    return entries


def read_file(project_name: str, path: str) -> dict:
    """Read file content (text files only, with size cap)."""
    target = _safe_path(project_name, path)
    if not target.is_file():
        raise ValueError(f"Not a file: {path}")

    ext = target.suffix.lower()
    if ext in _BINARY_EXTENSIONS:
        return {
            "path": path,
            "content": None,
            "binary": True,
            "size": target.stat().st_size,
            "truncated": False,
        }

    size = target.stat().st_size
    truncated = size > _MAX_FILE_SIZE

    try:
        if truncated:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_MAX_FILE_SIZE)
        else:
            content = target.read_text("utf-8", errors="replace")
    except Exception:
        return {
            "path": path,
            "content": None,
            "binary": True,
            "size": size,
            "truncated": False,
        }

    return {
        "path": path,
        "content": content,
        "binary": False,
        "size": size,
        "truncated": truncated,
    }


def get_git_status(project_name: str) -> dict[str, str]:
    """Run git status --porcelain and return {path: status_code} map."""
    root = _project_root(project_name)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-u"],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {}
    except Exception:
        return {}

    status_map: dict[str, str] = {}
    for line in result.stdout.strip().split("\n"):
        if not line or len(line) < 4:
            continue
        code = line[:2].strip()
        filepath = line[3:]
        # Handle renames: "R  old -> new"
        if " -> " in filepath:
            filepath = filepath.split(" -> ")[-1]
        status_map[filepath] = code

    return status_map


def _get_gitignored_files(project_name: str, directory: Path) -> set[str]:
    """Get set of gitignored file paths in a directory."""
    root = _project_root(project_name)
    try:
        result = subprocess.run(
            ["git", "ls-files", "--ignored", "--exclude-standard", "--others", str(directory)],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return set()
        return {str((root / line).resolve()) for line in result.stdout.strip().split("\n") if line}
    except Exception:
        return set()
