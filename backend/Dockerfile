# ==============================================================================
# AI Project Reviewer - API image
#
# Runtime dependencies only: requirements.txt no longer carries pytest or respx,
# so no test tooling ships to production.
#
# NOTE: no secret is baked in here. GITHUB_TOKEN and every other setting is read
# from the environment at container start, so the same image is used in every
# environment.
# ==============================================================================
FROM python:3.14-slim

# Bytecode files and stdout buffering both work against containers: the first
# writes files nothing reads, the second hides logs until a buffer fills.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, as their own layer: application edits then rebuild in
# seconds instead of re-resolving the dependency tree every time.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# A non-root user, because nothing here needs root and a container that is
# compromised should not also be privileged.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# `PORT` is honoured because most managed platforms assign one; it falls back to
# 8000 for Compose and local runs.
#
# `--proxy-headers` makes the app trust X-Forwarded-* from the reverse proxy in
# front of it, so client IPs and the request scheme are read correctly.
#
# One worker on purpose: the caches are in-process, so a second worker would
# hold a second, divergent copy and a repeat analysis could miss a cache the
# other worker filled. Scaling out needs a shared cache first.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
