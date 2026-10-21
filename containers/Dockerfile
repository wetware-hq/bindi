# Worker image: bindi orchestration against the hosted Boltz API.
#
#   docker build -f containers/Dockerfile -t bindi:local .
#
# No GPU, no model weights, no local generator: Boltz runs on https://api.boltz.bio and
# self-hosting it is not permitted. Supply BOLTZ_API_KEY at run time (never bake it in),
# and BINDI_APPR_APPROVAL if a real submission is intended.
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

# Hosted Boltz API. BOLTZ_API_KEY is NOT set here: pass it in at run time.
ENV BOLTZ_BASE_URL=https://api.boltz.bio

WORKDIR /work
ENTRYPOINT ["bindi"]
CMD ["--help"]
