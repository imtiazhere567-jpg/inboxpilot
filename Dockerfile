# Container image — used by Hugging Face Spaces (Docker SDK, port 7860) and works anywhere else that runs a container.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=7860

# Spaces run the container as uid 1000; give that user the app directory so nothing needs root.
RUN useradd -m -u 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .
USER app

EXPOSE 7860
CMD ["sh", "-c", "python scripts/init_db.py && uvicorn agent.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
