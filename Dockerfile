# SPDX-License-Identifier: GPL-3.0-or-later
# Generator image: python -m mscal (see docs/deployment.md).
# Multi-arch (linux/arm64 for Raspberry Pi 4/5, also amd64). Base: Python 3.14 (latest
# stable series with arm64 wheels for every dependency) on Debian 13 "trixie" (current
# Debian stable). All deps ship aarch64 wheels, so no compiler is needed in either stage.
FROM python:3.14-slim-trixie AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt /tmp/
RUN /venv/bin/pip install -r /tmp/requirements.txt \
 && rm -rf /venv/lib/python3.14/site-packages/motorsport_calendar/gui

FROM python:3.14-slim-trixie
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MSCAL_CONFIG=/config/config.yaml MSCAL_DATA=/data MSCAL_OUT=/www \
    XDG_CACHE_HOME=/tmp/.cache
# XDG_CACHE_HOME: safety net for library code that writes a default cache under $HOME
# (read-only here); /tmp is a tmpfs in docker-compose.yml.
COPY --from=build /venv /venv
COPY mscal /app/mscal
COPY LICENSE /app/LICENSE
WORKDIR /app
# Precompile all bytecode once at build time: the container runs read-only and each run
# is a short-lived process, so without this every run would recompile every module.
RUN python -m compileall -q -j 0 /venv /app/mscal \
 && useradd -u 10001 -M -s /usr/sbin/nologin mscal \
 && mkdir -p /data /www && chown mscal /data /www
USER 10001
# healthy = status.json rewritten within the last 7 hours
HEALTHCHECK --interval=10m --timeout=5s --start-period=10m \
  CMD ["python", "-c", "import os,sys,time; sys.exit(time.time()-os.path.getmtime('/www/status.json')>25200)"]
ENTRYPOINT ["python", "-m", "mscal"]
CMD ["run", "--loop"]
