# AttendQR served by waitress (run.py), with production dependencies only.
# The base image is pinned to a digest (a tag can be moved to other contents); Dependabot
# (.github/dependabot.yml) proposes the new digest when the image is rebuilt upstream.
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Exact versions from the lock file; no test or lint tools.
COPY requirements.lock .
RUN pip install -r requirements.lock

COPY attendqr ./attendqr
COPY migrations ./migrations
COPY templates ./templates
COPY static ./static
COPY run.py wsgi.py ./

RUN useradd --system --uid 10001 --no-create-home --home-dir /app attendqr \
    && mkdir -p /app/instance \
    && chown attendqr /app/instance
USER attendqr

# The SQLite database and the generated secret key live here; keep it on a volume.
VOLUME ["/app/instance"]

EXPOSE 5000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=2)"]

CMD ["python", "run.py"]
