FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# Keep apt and pip downloads in BuildKit cache mounts so rebuilds never fetch them twice.
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends ffmpeg
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY app ./app
# Precompile once at build time instead of on every container start.
RUN python -m compileall -q app
COPY web ./web
RUN mkdir -p /data /music
EXPOSE 8787
# Open audio streams would otherwise hold shutdown until Docker's 10 second kill timeout.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--timeout-graceful-shutdown", "3"]
