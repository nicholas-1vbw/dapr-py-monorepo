# syntax=docker/dockerfile:1
# Builds the shared wheelhouse:
#   /wheels/*.whl        workspace members and their locked runtime dependencies
#   /constraints.txt     exact versions from uv.lock
# Code generation runs here, so no generated files are needed on the host.

ARG PYTHON_VERSION
ARG UV_VERSION
ARG BUF_VERSION

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM bufbuild/buf:${BUF_VERSION} AS buf

FROM python:${PYTHON_VERSION}-slim AS base
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/build-venv
WORKDIR /src
# Project manifests only: enough to read the lock, so the layers below are
# rebuilt only when dependencies change, not when source code changes.
COPY pyproject.toml uv.lock ./
COPY --parents gen/*/pyproject.toml packages/*/pyproject.toml apps/*/pyproject.toml ./

# Locked third-party runtime dependencies (no development groups).
FROM base AS third-party
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    uv export --locked --no-default-groups --no-emit-workspace --no-hashes \
        --format requirements-txt --output-file /out/constraints.txt \
 && uv venv /opt/fetch \
 && uv pip install --python /opt/fetch/bin/python pip \
 && /opt/fetch/bin/python -m pip wheel --no-deps --wheel-dir /out/wheels \
        --requirement /out/constraints.txt

# Generated code and wheels of the workspace members.
FROM base AS workspace
COPY --from=buf /usr/local/bin/buf /usr/local/bin/buf
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --only-group codegen
COPY buf.yaml buf.lock buf.gen.yaml ./
COPY proto proto
RUN --mount=type=cache,target=/root/.cache/buf \
    PATH="/opt/build-venv/bin:${PATH}" buf generate
COPY packages packages
COPY apps apps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --all-packages --wheel --out-dir /out/wheels

FROM scratch AS wheels
COPY --from=third-party /out/ /
COPY --from=workspace /out/ /
