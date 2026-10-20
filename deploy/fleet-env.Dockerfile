# syntax=docker/dockerfile:1
#
# fleet-env: the layer-2 interface-descriptor environment for the
# NYESO1-A0201 binder fleet.
#
# This image IS the content-address environment. Its digest is wired into
# provenance.content_address in the fleet spec
# (binder-design/v3/NYESO1-A0201/infra.json), so every dependency is pinned
# with == in deploy/requirements-fleet-env.txt. Do not relax a pin to a floor:
# the digest would stop determining the descriptor numbers.
#
# Build:
#   docker build -f deploy/fleet-env.Dockerfile -t ghcr.io/wetware-hq/bindi-fleet-env:2026-10-20 .
# Verify:
#   docker run --rm ghcr.io/wetware-hq/bindi-fleet-env:2026-10-20 \
#     python -c "import gemmi, Bio, numpy, shape_complementarity"
#
# CPU-only. No GPU, no Boltz client, no network at run time: layer 2 reads
# artifacts (_predicted.cif, pae.npz, metrics.json) off the artifact store.

FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/wetware-hq/bindi" \
      org.opencontainers.image.title="bindi-fleet-env" \
      org.opencontainers.image.description="Pinned CPU environment for layer-2 interface descriptors (ipSAE, shape complementarity)" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/bindi

COPY deploy/requirements-fleet-env.txt /opt/bindi/deploy/requirements-fleet-env.txt
RUN pip install --no-cache-dir -r /opt/bindi/deploy/requirements-fleet-env.txt

# Fail the build, not a run three weeks later, if a pin stops importing.
RUN python -c "import gemmi, Bio, numpy, shape_complementarity as sc; \
print('gemmi', gemmi.__version__); \
print('biopython', Bio.__version__); \
print('numpy', numpy.__version__); \
print('shape_complementarity', sc.compute_sc.__name__)"

COPY pyproject.toml README.md LICENSE /opt/bindi/
COPY src /opt/bindi/src

RUN pip install --no-cache-dir --no-deps /opt/bindi

CMD ["python", "-c", "import gemmi, Bio, numpy, shape_complementarity; print('fleet-env ok')"]
