FROM python:3.11-slim

WORKDIR /app

# Install system deps + Node.js (for claude CLI) + GitHub CLI
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep curl ca-certificates gnupg openssh-client \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs gh \
 && npm install -g @anthropic-ai/claude-code \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY backend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 4040

ENV CLAUDE_DATA_DIR=/root/.claude
# npm install -g puts `claude` on PATH at /usr/local/bin/claude
ENV CLAUDE_BIN=claude
# GitHub PAT for gh CLI and git HTTPS inside agents — injected at runtime via docker-compose
# ENV GH_TOKEN and GITHUB_TOKEN set in docker-compose.yml or scripts/start.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "4040", "--log-level", "info"]
