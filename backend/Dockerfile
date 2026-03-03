FROM python:3.11-slim

WORKDIR /app

# Install OS deps + Node.js (needed to run the claude CLI)
RUN apt-get update \
 && apt-get install -y --no-install-recommends procps lsof git curl \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*

# Install claude CLI — npm fetches the correct Linux binary for this arch
RUN npm install -g @anthropic-ai/claude-code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

EXPOSE 4040

ENV CLAUDE_BIN=/usr/local/bin/claude \
    CLAUDE_DATA_DIR=/root/.claude

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "4040", "--log-level", "info"]
