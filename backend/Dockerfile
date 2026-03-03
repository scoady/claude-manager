FROM python:3.11-slim

WORKDIR /app

# ripgrep for fast file search (used by ToolExecutor Grep tool)
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

EXPOSE 4040

ENV CLAUDE_DATA_DIR=/root/.claude

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "4040", "--log-level", "info"]
