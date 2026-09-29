FROM python:3.12-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HEADLESS=true \
    TRADING_MODE=DEMO \
    DRY_RUN=true \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

# Persistent profile + artifacts should be mounted at runtime.
VOLUME ["/app/browser_profile", "/app/screenshots", "/app/logs", "/app/data", "/app/config"]

EXPOSE 8000

# Default: dashboard only. Order execution is not enabled in Phase 1–2.
CMD ["python", "main.py", "--dashboard"]
