# Apps

An app is a deployable process: one console script, one Dapr app id per deployment. Apps are thin:
they read settings, wire packages together and start the server. Behaviour lives in packages.

## Anatomy

```text
apps/counter/
├── pyproject.toml            # dependencies and the console script
└── src/counter_app/main.py   # settings, requirements, build_app(), main()
```

```python
class CounterSettings(RuntimeSettings):  # APP_* environment variables
    name: str = "counter"
    statestore_name: str = "statestore"


def build_app() -> FastAPI:
    settings = CounterSettings()

    async def on_startup(runtime: Runtime) -> None:  # after Dapr checks passed
        runtime.state["store"] = DaprStateStore(runtime.dapr, settings.statestore_name)

    app = create_app(settings, requirements=requirements(settings), on_startup=on_startup)
    mount_connect(app, CounterServiceASGIApplication(...))
    return app


def main() -> None:
    run("counter_app.main:build_app", CounterSettings())
```

- `requirements` lists the Dapr components the app needs (name, category, capabilities). The app
  does not serve until they are loaded. An app without component needs passes nothing.
- `run()` serves the factory with uvicorn; `APP_RELOAD=true` enables reload for development.

An app that serves its services over gRPC instead (`apps/counter-grpc`) has no FastAPI app: its
`main()` calls `run_grpc(settings, services, requirements=...)` with the same settings and
requirements, and `deploy/apps` declares `appProtocol: grpc`. It does not reload on code changes.
See [Serve a service over gRPC](services.md#serve-a-service-over-grpc).

## Add an app

Example: a `billing` app.

1. Create `apps/billing/pyproject.toml`:

   ```toml
   [project]
   name = "billing"
   version = "0.1.0"
   requires-python = ">=3.14"
   dependencies = ["app-runtime", "orders"]

   [project.scripts]
   billing = "billing_app.main:main"

   [tool.uv.sources]
   app-runtime = { workspace = true }
   orders = { workspace = true }

   [build-system]
   requires = ["uv_build>=0.13.7,<0.14"]
   build-backend = "uv_build"

   [tool.uv.build-backend]
   module-name = "billing_app"
   ```

2. Write `apps/billing/src/billing_app/main.py` following the anatomy above.

3. Add `billing` to the root `pyproject.toml` dependencies and sources, then `uv lock && uv sync`.

4. Add the image. Copy `deploy/docker/gateway/default.Dockerfile` to
   `deploy/docker/billing/default.Dockerfile` and replace every `gateway` with `billing` (the
   header comment, the `uv pip install` line and the `ENTRYPOINT`). Then add a target to `docker-bake.hcl` and to the
   default group:

   ```hcl
   group "default" {
     targets = ["gateway", "counter", "billing"]
   }

   target "billing" {
     inherits   = ["_app"]
     dockerfile = "deploy/docker/billing/default.Dockerfile"
     tags       = ["${IMAGE_PREFIX}/billing:${TAG}"]
   }
   ```

5. Add the deployment input `deploy/apps/billing.yaml`:

   ```yaml
   appId: billing
   member: billing
   bakeTarget: billing
   command: billing
   appPort: 8080
   appProtocol: http
   daprConfig: app-config
   env:
     APP_SOME_SETTING: value
   # expose:
   #   http: 8081            # only if the app must be reachable from the host
   ```

   `poe dev` now starts it with a sidecar, and `poe kind up` / `poe e2e` build and deploy it.

6. If the app uses a component, add the app id to the component's `scopes` in
   `deploy/environment/components.yaml` (see [Infrastructure and components](components.md)).

## Deployment inputs

`deploy/apps/<deployment>.yaml` is read by the framework and passed to Helm as values of the generic
chart `deploy/platform/charts/app`:

| Field | Used by | Meaning |
| --- | --- | --- |
| `appId` | all | Dapr app id; also the Helm release and Deployment name |
| `member`, `command` | `dev` | workspace member and console script to run |
| `bakeTarget` | `kind`, `e2e` | image target to build |
| `appPort` | `kind` | container port of the app in Kubernetes (`poe dev` listens on `expose.http`, or on a free port) |
| `appProtocol` | all | `http` (default) or `grpc`: protocol the sidecar uses to reach the app; also decides the probes and the protocol of the member's integration test sidecar |
| `daprConfig` | all | Dapr Configuration (tracing) to attach, defined in `deploy/platform/charts/dapr-resources`; empty means none (native sidecars then get an empty Configuration, never the machine's default) |
| `env` | all | `APP_*` settings |
| `expose` | `dev`, `kind` | `{http: <host port>}`: reach the app from the host on that port. Other apps get no Service and are reached through Dapr |

Each environment adds its own overrides to these values instead of copying them: the image
reference, the environment variables of the infrastructure (`appEnv` in `infra.yaml`) and, for
exposed apps, a NodePort Service. A production deployment passes its own overrides to the same
chart (see [Deploying](deploying.md)).

The same app can be deployed several times with different app ids by adding another file in
`deploy/apps/` that refers to the same `member` and `bakeTarget`.

## Probes and startup

For HTTP apps the chart configures a startup probe on `/healthz` (up to two minutes), readiness on
`/readyz` and liveness on `/healthz`. gRPC apps get the same probes as TCP checks of the app port,
which opens only after the startup checks passed (`poe dev` waits for the port in the same way).
The app port is named `http` in Kubernetes whatever the protocol. Waiting for the sidecar and for the required components shares one deadline,
`APP_STARTUP_TIMEOUT_SECONDS` (60 by default); keep it below the startup probe budget so that an app
that cannot start exits with its reason in the log before Kubernetes restarts it. Both endpoints answer only after the Dapr startup checks passed; a missing
component keeps the Pod unready and the container restarts with the reason in its log.
