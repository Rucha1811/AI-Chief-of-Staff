FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# D5: production runs as an unprivileged user (uid 10001), declared by
# `user:` in docker-compose.prod.yml. The account is created here but NOT
# switched to - `USER` would also apply to the development stack, where the
# ./backend bind mount is owned by the host user and would stop being
# writable.
#
# uploads/, cache/ and vapid/ are excluded from the build context by
# .dockerignore, so they do not exist after COPY. Recreating them here serves
# two purposes: they are the mountpoints docker-compose.prod.yml puts volumes
# over (which must exist in the image for a read-only root filesystem), and
# they belong to the runtime uid so those volumes initialise with ownership
# the container can actually write to. /app itself stays root-owned and
# read-only - nothing in the app writes outside these three directories and
# /tmp (a tmpfs).
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app appuser \
    && mkdir -p /app/uploads /app/cache /app/vapid \
    && chown -R appuser:appuser /app/uploads /app/cache /app/vapid

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
