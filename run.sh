#!/usr/bin/env zsh
# Start the claude-manager backend locally.
# The frontend (nginx in k8s) proxies /api/ and /ws to localhost:4040.

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

export CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
export CLAUDE_DATA_DIR="${CLAUDE_DATA_DIR:-$HOME/.claude}"
export MANAGED_PROJECTS_DIR="${MANAGED_PROJECTS_DIR:-$HOME/git/claude-managed-projects}"

cd "$REPO_DIR"

if [[ ! -d .venv ]]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
fi

source .venv/bin/activate

pip install -q -r requirements.txt

mkdir -p "$MANAGED_PROJECTS_DIR"

echo "Backend starting on http://0.0.0.0:4040"
echo "  CLAUDE_BIN=$CLAUDE_BIN"
echo "  CLAUDE_DATA_DIR=$CLAUDE_DATA_DIR"
echo "  MANAGED_PROJECTS_DIR=$MANAGED_PROJECTS_DIR"

exec uvicorn backend.main:app --host 0.0.0.0 --port 4040 --log-level info
