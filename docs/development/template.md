# Using this template

The repository is meant to be copied and turned into your own project. The example domain
(`greeting`, with the `gateway`, `counter` and `counter-grpc` apps) shows every piece working;
replace it with yours.

## Names to change

| Name | Where |
| --- | --- |
| Project name `dapr-py-monorepo` | root `pyproject.toml` (`name`, also the kind cluster name), `docker-bake.hcl` (`IMAGE_PREFIX`), README |
| Proto namespace `example` | `proto/example/`, `package` lines in `.proto` files, imports of `contracts.example...` |
| Domain package `greeting` | `packages/greeting/`, its imports, dependencies of the apps |
| Apps `gateway`, `counter`, `counter-grpc` | `apps/`, `deploy/apps/*.yaml`, `deploy/docker/<app>/`, `docker-bake.hcl`, component `scopes`, the gateway's `APP_COUNTER_*_APP_ID` settings, `tests/integration/counter-grpc/`, endpoint names (`<app>.http`) and scenario text in `tests/e2e/` |
| Infrastructure and components | `deploy/environment/infra.yaml`, `deploy/environment/components.yaml`, `APP_*_NAME` settings in `deploy/apps/`, component names in `tests/integration/` |

Keep `app-runtime`, `contracts` and `proto/platform/`; they belong to the framework, not to the
example.

## Replacing the example

Replacing the example changes the system's code and its environment description only (see
[Layers and ownership](../architecture.md#layers-and-ownership)). Nothing in `scripts/`,
`deploy/platform/` or `deploy/docker/wheels.Dockerfile` needs to change while the system uses the
component types and resource kinds the platform supports. Using another one (for example a
PostgreSQL state store) means extending the platform once, as described in
[Add a component](components.md#add-a-component).

1. Write your contracts under `proto/<namespace>/...` and delete `proto/example/` (keep
   `proto/platform/`, the contract of the framework's own tests).
2. Create your domain packages ([Packages](packages.md)) and apps ([Apps](apps.md)).
3. Describe your infrastructure and components in `deploy/environment/`
   ([Infrastructure and components](components.md)), and update `deploy/apps/`, the Bake targets
   and `deploy/docker/<app>/` for your apps.
4. Replace the integration tests in `tests/integration/greeting/` and
   `tests/integration/counter-grpc/` and the scenarios in `tests/e2e/` with your own; keep
   `tests/integration/app-runtime/` (it uses whatever components you declare).
5. Delete `packages/greeting/`, `apps/gateway/`, `apps/counter/`, `apps/counter-grpc/` and their
   entries in the root `pyproject.toml`, then `uv lock`.
6. Run `uv run --only-group codegen poe setup`, `uv run ruff format .` (renames change line
   lengths), `uv run poe check`, `uv run poe integration` and `uv run poe e2e`.

## Without gRPC

Serving over gRPC is optional. To drop it from the example: delete `apps/counter-grpc/`,
`deploy/apps/counter-grpc.yaml`, `deploy/docker/counter-grpc/`, its `docker-bake.hcl` target,
`tests/integration/counter-grpc/` and its entries in the root `pyproject.toml`; remove
`counter-grpc` from the state store's `scopes` (and `keyPrefix: name` if nothing else shares
state); let the gateway call only the counter app over Connect and remove the gRPC scenarios from
`tests/e2e/`. `app_runtime.grpc_server` stays available for later.

## Keeping it current

- Python dependencies: `uv lock --upgrade` (or `--upgrade-package NAME`), then run the checks.
- Dapr: update `dapr.runtimeVersion` and `dapr.chartVersion` in `deploy/platform/versions.yaml`, run
  `dapr init --slim --runtime-version <version>`, and upgrade the `dapr` SDK in `app-runtime`.
- Buf and protoc plugins: `buf.version` in `versions.yaml`, the `codegen` group in `pyproject.toml`,
  `buf dep update` for `buf.lock`.
- Infrastructure images: `image` of each instance in `deploy/environment/infra.yaml`.
- CI tool versions: `helm`, `kind` and `kubectl` in `deploy/platform/versions.yaml`.
