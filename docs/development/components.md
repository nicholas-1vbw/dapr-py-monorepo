# Infrastructure and Dapr components

The system describes what it needs in `deploy/environment/`. The framework turns that description
into running infrastructure and Dapr resources for every local environment, and the same component
definitions are used in production.

| What | Where | Owner |
| --- | --- | --- |
| Infrastructure instances (Redis, PostgreSQL, Jaeger, ...) | `deploy/environment/infra.yaml` | the system |
| Files mounted into instances (init scripts, configuration) | `deploy/environment/files/` | the system |
| Dapr components and their scopes | `deploy/environment/components.yaml` | the system |
| Which components an app requires at startup | the app's `requirements(...)` | the system |
| Provider templates (how a component type is configured) | `deploy/platform/charts/dapr-resources/templates/components.yaml` | the framework |
| Running instances in Docker and Kubernetes | `scripts/framework/docker.py`, `scripts/framework/kubernetes.py`, `deploy/platform/charts/infra` | the framework |

## Lifecycle per environment

| Environment | Instances | Components | Scopes |
| --- | --- | --- | --- |
| `poe dev` | those enabled for `dev`, in a Compose project of the session | rendered to `.run/<run-id>/resources/` | kept |
| `poe integration` | those enabled for `integration`, one Compose project per run, shared by all package tasks | rendered once, read-only | omitted (test sidecars have generated app ids) |
| `poe kind up`, `poe e2e` | those enabled for `kind`, through the infra chart | `dapr-resources` Helm release | kept |
| Production | provided by the platform | the same chart with `components.yaml` and the environment's `resources-values.yaml` | kept |

Every local setup writes `.run/<run-id>/resources-values.yaml`, which says where its instances are.
Production provides its own; `deploy/platform/resources-values.example.yaml` documents the format.

## Describe an infrastructure instance

The `resource` of an instance must be of a kind the `dapr-resources` chart supports: `redis` and
`otlp` today. A new kind is a platform extension (see [Add a component](#add-a-component), step 2);
the PostgreSQL instance below assumes that extension has been made.

```yaml
# deploy/environment/infra.yaml
orders-db:
  image: postgres:17.6-alpine            # always pin
  env:
    POSTGRES_PASSWORD: local
  ports:
    postgres: 5432                       # named container ports
  files:
    orders-init.sql: /docker-entrypoint-initdb.d/init.sql   # from deploy/environment/files/
  ready:
    command: [pg_isready, -U, postgres]  # exit 0 = ready
  environments: [dev, integration, kind]
  resource:                              # entry in resources-values.yaml
    name: orders-db
    value:
      kind: postgres
      host: "{host}"
      port: "{port:postgres}"
      database: orders
```

| Field | Meaning |
| --- | --- |
| `image`, `args`, `env` | container image, arguments and environment |
| `ports` | named container ports; names are used by `expose`, `{port:<name>}` and Kubernetes |
| `files` | files from `deploy/environment/files/` mounted read-only at the given paths |
| `ready` | command run in the container; the environment waits until it succeeds |
| `environments` | `dev`, `integration`, `kind` (default: all) |
| `expose` | `{port name: host port}`, reachable from the host in `dev` and `kind` |
| `resource` | how Dapr components refer to the instance (`name`) and connect to it (`value`) |
| `appEnv` | environment variables given to every app, for example a collector address |

In `resource` and `appEnv`, `{host}` and `{port:<name>}` are replaced per target: `127.0.0.1` and
the published port in Docker, `<instance>.<namespace>.svc.cluster.local` and the container port in
Kubernetes. Several components can use the same instance; an instance can also serve no component
(a tracing backend that only gives apps an `appEnv`).

The description covers what local infrastructure usually needs. An instance that needs more (a
volume claim, several containers, special security settings) is beyond it and needs a framework
extension: a new field in `infra.yaml` handled by `scripts/framework/docker.py` and
`deploy/platform/charts/infra`, so that both targets keep deploying it from the same description.

## Add a component

Example: a pub/sub component on the existing Redis instance.

1. Define it in `deploy/environment/components.yaml`:

   ```yaml
   components:
     pubsub:
       type: pubsub.redis
       version: v1
       resource: redis              # `resource.name` of an instance
       scopes: [orders, billing]    # app ids allowed to use it
   ```

2. If the type is new to the framework, add it to the `type` enum in
   `deploy/platform/charts/dapr-resources/values.schema.json` and a branch in
   `templates/components.yaml` that maps the resource to the component's metadata (the
   `state.redis` branch is the model). A new resource `kind` also goes into the `resources` schema
   and `deploy/platform/resources-values.example.yaml`.

3. Declare the requirement in the app that uses it:

   ```python
   ComponentRequirement(name=settings.pubsub_name, type_prefix="pubsub.")
   ```

4. Use it through the Dapr SDK in a package, behind a small interface you can fake in unit tests.
   Subscriptions in FastAPI come from `dapr[fastapi]` (`dapr.ext.fastapi.DaprApp`), already a
   dependency of `app-runtime`.

5. Add an integration test for the interaction in `tests/integration/<package>/`.

`uv run poe lint` checks that every component's `resource` is provided by an instance enabled in
each local environment and that every scope is an app id in `deploy/apps/`.

## Switching providers between environments

An environment can use a different provider for the same component name, for example a managed
cloud state store in production. That is a different `type` with different metadata: add a provider
branch to the chart and give that environment its own components file. The app does not change as
long as the new provider offers the capabilities the app requires (the startup check verifies
this).

## Sharing state between apps

Dapr prefixes state keys with the app id by default, so two apps never see each other's keys.
`statestore` sets `keyPrefix: name` in `metadata` instead: keys are prefixed with the component
name, so the `counter` and `counter-grpc` apps (one service over two protocols) share their
counters. Changing `keyPrefix` changes the stored keys (for example from `counter||Alice` to
`statestore||Alice`): data written before is no longer found and needs a migration. Without a
reason to share, keep the default.

## Scopes and secrets

- `scopes` lists the app ids allowed to load a component. It is an authorization list, not a
  dependency list: the app's requirements decide what it needs.
- In clusters, use `passwordSecret` (a Kubernetes Secret reference) in `resources-values.yaml`.
  Native sidecars cannot read Kubernetes Secrets; local instances run without passwords.
