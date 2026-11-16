# Image build entry point. Build with `uv run poe images`, which supplies the
# versions below from .python-version, the running uv and deploy/platform/versions.yaml.

variable "PYTHON_VERSION" {}
variable "UV_VERSION" {}
variable "BUF_VERSION" {}

variable "IMAGE_PREFIX" {
  default = "dapr-py-monorepo"
}

variable "TAG" {
  default = "dev"
}

group "default" {
  targets = ["gateway", "counter", "counter-grpc"]
}

target "_common" {
  context = "."
  args = {
    PYTHON_VERSION = PYTHON_VERSION
    UV_VERSION     = UV_VERSION
    BUF_VERSION    = BUF_VERSION
  }
}

# Shared artifact: wheels of all workspace members and their locked runtime
# dependencies, plus a constraints file. Consumed by every app image.
target "wheels" {
  inherits   = ["_common"]
  dockerfile = "deploy/docker/wheels.Dockerfile"
}

# App images install from the shared wheelhouse.
target "_app" {
  inherits = ["_common"]
  contexts = {
    wheels = "target:wheels"
  }
}

target "gateway" {
  inherits   = ["_app"]
  dockerfile = "deploy/docker/gateway/default.Dockerfile"
  tags       = ["${IMAGE_PREFIX}/gateway:${TAG}"]
}

target "counter" {
  inherits   = ["_app"]
  dockerfile = "deploy/docker/counter/default.Dockerfile"
  tags       = ["${IMAGE_PREFIX}/counter:${TAG}"]
}

target "counter-grpc" {
  inherits   = ["_app"]
  dockerfile = "deploy/docker/counter-grpc/default.Dockerfile"
  tags       = ["${IMAGE_PREFIX}/counter-grpc:${TAG}"]
}
