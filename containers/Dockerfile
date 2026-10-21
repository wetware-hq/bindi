# Worker image: bindi orchestration and the CPU scoring/gating toolchain.
#
#   docker build -f containers/Dockerfile -t bindi:local .
#
# No design model is installed here and no model weights are downloaded. bindi designs
# through the HOSTED Boltz API (see src/bindi/boltz.py); the only secret the worker
# needs at run time is BOLTZ_API_KEY, which is passed in at run time and never baked in.
#
# CONTENT-ADDRESS WARNING — READ BEFORE BUILDING.
# The fleet's content address pins
#   ghcr.io/wetware-hq/bindi-fleet-env@sha256:7e8762aebc3390611aafe0b67d925e567199d519e638113a80da33b86246a8e6
# That digest was built BEFORE the self-hosted design layer was removed from this file
# (2026-10-21). Any rebuild from this Dockerfile therefore produces a DIFFERENT digest.
# That is expected, not a fault — but a new digest is a new content address: record it
# afresh in the fleet spec alongside the old one, do not overwrite 7e8762ae, and do not
# claim the new image is the one the existing records refer to. Nothing in this change
# rebuilds or repushes an image.
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

# Pinned aligners (Chief Scientist thresholds are sensitive at identity boundaries).
# Do not rely on the base image's apt versions: install explicit releases instead.
ARG MMSEQS_RELEASE=15-6f452
ARG BLAST_VERSION=2.16.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL \
        "https://github.com/soedinglab/MMseqs2/releases/download/${MMSEQS_RELEASE}/mmseqs-linux-avx2.tar.gz" \
        -o /tmp/mmseqs.tar.gz \
    && tar xzf /tmp/mmseqs.tar.gz -C /tmp \
    && install -m 0755 "/tmp/mmseqs/bin/mmseqs" /usr/local/bin/mmseqs \
    && curl -fsSL \
        "https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/2.16.0/ncbi-blast-${BLAST_VERSION}+-x64-linux.tar.gz" \
        -o /tmp/ncbi-blast.tar.gz \
    && tar xzf /tmp/ncbi-blast.tar.gz -C /opt \
    && ln -sf "/opt/ncbi-blast-${BLAST_VERSION}+/bin/blastp" /usr/local/bin/blastp \
    && ln -sf "/opt/ncbi-blast-${BLAST_VERSION}+/bin/makeblastdb" /usr/local/bin/makeblastdb \
    && rm -rf /tmp/mmseqs.tar.gz /tmp/mmseqs /tmp/ncbi-blast.tar.gz \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# bindi gates invoke `mmseqs` and `blastp` on PATH (see src/bindi/gates.py).
ENV BINDI_MMSEQS_CMD=mmseqs
ENV BINDI_BLASTP_CMD=blastp
ENV BINDI_MAKEBLASTDB_CMD=makeblastdb

WORKDIR /opt/bindi
COPY pyproject.toml README.md LICENSE /opt/bindi/
COPY src /opt/bindi/src
COPY campaigns /opt/bindi/campaigns
COPY fixtures /opt/bindi/fixtures
RUN pip install --no-cache-dir /opt/bindi

WORKDIR /work
ENTRYPOINT ["bindi"]
CMD ["--help"]
