# Images

```bash
uv run poe images                 # all app images, loaded into Docker with tag "dev"
uv run poe images counter --tag x # one target
```

`docker-bake.hcl` is the only place that defines image targets. `poe images` passes the versions
(Python from `.python-version`, uv from the running uv, Buf from `deploy/platform/versions.yaml`) and
writes the resulting references to `.run/images/images.json`.

## How an image is built

1. **`wheels` target** (`deploy/docker/wheels.Dockerfile`), shared by all apps:
   - `third-party` stage: exports the locked runtime dependencies from `uv.lock` (no development
     groups) and downloads their wheels. It only sees the project manifests and the lock, so
     changing source code does not invalidate it.
   - `workspace` stage: installs the locked protoc plugins, runs `buf generate`, and builds wheels
     of all workspace members.
   - Output: `/wheels/*.whl` and `/constraints.txt`.
2. **App targets** (`deploy/docker/<app>/default.Dockerfile`) receive `wheels` as a named build
   context and install the app package offline with the constraints. A dependency without a wheel
   fails the build.
3. The result has a virtual environment in `/opt/venv`, runs as uid 10001, listens on 8080 and
   starts the app's console script. Source code, build tools, development dependencies and the
   wheelhouse are not in the image.

All targets use the repository root as build context; `.dockerignore` keeps local environments,
generated code, tests and run records out of it.

## Variants

Each app has a `default.Dockerfile` in `deploy/docker/<app>/`. When an image of the app needs
something its package metadata cannot express (system libraries, external binaries, a different
base image), add a variant next to it and a target that still consumes the wheelhouse:

```text
deploy/docker/counter/
├── default.Dockerfile
└── debug.Dockerfile
```

```hcl
target "counter-debug" {
  inherits   = ["_app"]
  dockerfile = "deploy/docker/counter/debug.Dockerfile"
  tags       = ["${IMAGE_PREFIX}/counter-debug:${TAG}"]
}
```

A deployment selects the variant with `bakeTarget: counter-debug` in `deploy/apps/`.

Pin versions and checksums of non-Python downloads in that Dockerfile. Python dependencies stay in
package metadata; an image selects packages (and extras), it does not repeat their dependencies.

## Platforms and publishing

Images are built for the platform of the machine running Docker, which is what kind needs. To
publish, build the platforms your clusters use and push them under your registry:

```bash
uv run poe images --prefix registry.example.com/team --tag 1.4.0 \
  --platform linux/amd64,linux/arm64 --push
```

The wheelhouse is built per platform, so native wheels always match the image. See
[Deploying](deploying.md) for the rest of a cluster deployment.
