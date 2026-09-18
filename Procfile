web: python scripts/init_db.py && uvicorn agent.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"
