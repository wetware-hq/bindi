# Worker image: bindi orchestration on top of a BoltzGen runtime.
#
# Option A — use a pre-built BoltzGen image that provides `boltzgen` on PATH:
#   docker build -f containers/Dockerfile -t bindi:local .
#
# Option B — set BASE_IMAGE to any Python image and install BoltzGen via pip (GPU host required).
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

RUN pip install --no-cache-dir "boltzgen>=0.3.2"

WORKDIR /opt/bindi
COPY pyproject.toml README.md LICENSE /opt/bindi/
COPY src /opt/bindi/src
COPY campaigns /opt/bindi/campaigns
COPY fixtures /opt/bindi/fixtures
RUN pip install --no-cache-dir /opt/bindi

ENV BOLTZGEN_CMD=boltzgen
# BoltzGen model weights download on first run (~6 GB Boltz + optional ESMFold2 cache).
ENV BOLTZ_CACHE=/cache

WORKDIR /work
ENTRYPOINT ["bindi"]
CMD ["--help"]
