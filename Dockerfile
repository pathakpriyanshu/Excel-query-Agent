FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY . .

# Run as a non-root user for safety.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# Chainlit serves here. DigitalOcean injects $PORT (defaults to 8080);
# fall back to 8000 for local `docker run`.
EXPOSE 8000

CMD chainlit run app.py --host 0.0.0.0 --port ${PORT:-8000} --headless
