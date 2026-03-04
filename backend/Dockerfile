FROM python:3.11-slim

WORKDIR /app

# Install system deps + Node.js (for claude CLI)
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep curl ca-certificates gnupg openssh-client \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
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

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "4040", "--log-level", "info"]
