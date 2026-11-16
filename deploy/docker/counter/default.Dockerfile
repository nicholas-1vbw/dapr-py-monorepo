# syntax=docker/dockerfile:1
# Default image of the counter app. The app is installed offline from the shared
# wheelhouse (build context `wheels`); the image contains only the virtual
# environment, no source tree, build tools or development dependencies.
#
# Variants of the app (extra system packages, files or another base image) get
# their own Dockerfile in this directory.

ARG PYTHON_VERSION
ARG UV_VERSION

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim
RUN useradd --system --uid 10001 --no-create-home app
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=bind,from=wheels,target=/wheelhouse \
    uv venv --python /usr/local/bin/python3 /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-index \
        --find-links /wheelhouse/wheels --constraint /wheelhouse/constraints.txt \
        counter
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8080
USER 10001
EXPOSE 8080
ENTRYPOINT ["counter"]
