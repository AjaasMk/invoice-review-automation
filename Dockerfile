FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/runs /app/runs/inbox /app/runs/intake_storage /app/runs/uploads \
    && useradd --create-home appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000
CMD ["python", "-m", "web.server"]
