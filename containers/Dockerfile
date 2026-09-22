# Worker image: bindi orchestration on top of a BindCraft2 runtime.
# Build BindCraft2 first (see https://github.com/PacesaLab/BindCraft2/containers),
# tag it bindcraft:local, then:
#   docker build -f containers/Dockerfile -t bindi:local .
#
# Or set BASE_IMAGE to any image that provides `bindcraft` on PATH.

ARG BASE_IMAGE=bindcraft:local
FROM ${BASE_IMAGE}

WORKDIR /work

COPY pyproject.toml README.md LICENSE /opt/bindi/
COPY src /opt/bindi/src
COPY campaigns /opt/bindi/campaigns

RUN pip install --no-cache-dir /opt/bindi

ENV BINDCRAFT_CMD=bindcraft
ENTRYPOINT ["bindi"]
CMD ["recruit"]
