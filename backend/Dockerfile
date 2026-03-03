FROM python:3.11-slim

WORKDIR /app

# Install OS deps needed for process inspection (ps, lsof)
RUN apt-get update \
 && apt-get install -y --no-install-recommends procps lsof \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

EXPOSE 4040

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "4040", "--log-level", "info"]
