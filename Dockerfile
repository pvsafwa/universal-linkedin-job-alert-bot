# Minimal Hardened Python Container for Universal LinkedIn Job Alert Bot
FROM python:3.11-alpine

# Set non-buffering for stdout/stderr logs
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create non-root application user and group (UID 10001)
RUN addgroup -g 10001 appgroup && \
    adduser -u 10001 -G appgroup -s /bin/sh -D appuser

# Set working directory
WORKDIR /app

# Create persistent data directory for SQLite database
RUN mkdir -p /app/data && \
    chown -R appuser:appgroup /app

# Copy application script
COPY --chown=appuser:appgroup universal_job_alert_bot.py /app/universal_job_alert_bot.py

# Switch to hardened non-root user
USER 10001:10001

# Run bot daemon
CMD ["python3", "/app/universal_job_alert_bot.py"]
