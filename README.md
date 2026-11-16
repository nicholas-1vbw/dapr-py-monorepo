# Dapr Python Monorepo

A minimal, runnable template for building Python services on [Dapr](https://dapr.io/). It contains
just enough code to show every piece working together:

- **Services**: contracts in Protobuf managed by [Buf](https://buf.build/), served and called with
  [Connect for Python](https://connectrpc.com/docs/python/getting-started/), request validation with
  [Protovalidate](https://protovalidate.com/), a public HTTP API with FastAPI.
- **Dapr**: service invocation between apps, a state store, startup checks of required
  components, tracing across apps and sidecars.
- **Tests in three layers**: unit tests without Dapr, integration tests with a real sidecar, and
  BDD end-to-end tests on Kubernetes.
- **Images and deployment**: one shared wheelhouse built with Docker Bake, small app images, Helm
  charts, and a local Kubernetes environment on kind.

Copy it, replace the example domain, and add your own packages and services by following
[docs/development](docs/development/).

## The example

```text
curl ──HTTP──▶ gateway ──▶ Dapr sidecar ══ service invocation ══▶ Dapr sidecar ──▶ counter ──▶ Redis
                  │              │                                     │              │   (state store)
                  └──────────────┴────────────── traces ───────────────┴──────────────┴──▶ Jaeger
```

- **gateway** serves `POST /greet` and asks the counter for the visit number.
- **counter** serves `CounterService` (Connect RPC) and stores counters in a Dapr state store,
  using ETags so that concurrent visits are never lost.

```bash
$ curl -X POST localhost:8080/greet -H 'content-type: application/json' -d '{"name":"Alice"}'
{"message":"Hello, Alice! You are visitor #1.","visits":1}
```

## What this repository does not cover

The Dapr control plane, databases and observability backends of production environments are
provided by the platform that runs the apps. This repository produces the app images, the Helm
charts and the Dapr resource definitions, and documents the inputs a production environment
supplies. Local environments (Docker Compose and kind) exist for development and tests only.

## Prerequisites

| Tool | Needed for |
| --- | --- |
| [uv](https://docs.astral.sh/uv/) ≥ 0.13.7 | everything (it installs Python 3.14 when missing) |
| [Buf CLI](https://buf.build/docs/cli/installation/) | code generation |
| [Helm](https://helm.sh/docs/intro/install/) 4 | `check`, rendering Dapr resources, kind |
| [Dapr CLI](https://docs.dapr.io/getting-started/install-dapr-cli/) + `dapr init --slim --runtime-version 1.19.1` | `dev`, `integration` |
| [Docker](https://docs.docker.com/get-docker/) with Compose and Buildx | `dev`, `integration`, images, kind, `e2e` |
| [kind](https://kind.sigs.k8s.io/) and [kubectl](https://kubernetes.io/docs/tasks/tools/) | `kind`, `e2e` |

Each task checks only the tools it uses. CI verifies the repository on Linux x86_64.

## Quick start

```bash
# 1. Generate code and install the workspace (once after cloning)
uv run --only-group codegen poe setup

# 2. Run both apps locally with Dapr sidecars, Redis and Jaeger
uv run poe dev

# 3. In another terminal
curl -X POST localhost:8080/greet -H 'content-type: application/json' -d '{"name":"Alice"}'
open http://localhost:16686   # traces in Jaeger
```

On Kubernetes:

```bash
uv run poe kind up      # build images, create the cluster, deploy everything
curl -X POST localhost:8080/greet -H 'content-type: application/json' -d '{"name":"Alice"}'
uv run poe kind down
```

## Tasks

| Command | What it does |
| --- | --- |
| `uv run poe proto` | generate Python code from `proto/` |
| `uv run poe check` | lint (Buf, Ruff, Pyright, charts), unit tests and the framework's own tests |
| `uv run poe unit` | unit tests only; no Dapr or Docker needed |
| `uv run poe integration [--workers N] [--packages NAME ...]` | integration tests with real sidecars |
| `uv run poe dev [--apps NAME ...]` | run apps natively with sidecars and local infrastructure |
| `uv run poe images [TARGET ...]` | build images with Docker Bake |
| `uv run poe kind up` / `kind down` | local Kubernetes environment |
| `uv run poe e2e` | end-to-end BDD tests on a fresh kind cluster |
| `uv run poe teardown --run-id ID` | remove what an interrupted run left behind |

## Repository layout

```text
proto/                 Protobuf contracts (source of truth for RPC messages and rules)
gen/python/            generated `contracts` package (code is generated, not committed)
packages/app-runtime/  shared startup, logging, tracing, validation and Dapr checks
packages/greeting/     the example domain: gateway API and counter service
apps/gateway, counter  thin app entry points
tests/integration/     integration tests, one Dapr sidecar per package
tests/e2e/             BDD scenarios on kind
deploy/environment/    what the system needs: infrastructure instances and Dapr components
deploy/apps/           app instances: ports, settings, exposure, image targets
deploy/platform/       how it is deployed: Helm charts, kind topology, tool versions
deploy/docker/         Dockerfiles (shared wheelhouse and one per app)
scripts/               `poe` tasks (tasks/) on framework building blocks (framework/)
docs/                  architecture and development guides
```

## Documentation

- [Architecture](docs/architecture.md): how the pieces fit together and why.
- [Development guides](docs/development/): getting started, services, packages, apps,
  infrastructure and components, deploying, observability, testing, images, CI, and adapting the
  template.

## License

[Apache License 2.0](LICENSE)
