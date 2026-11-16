# Getting started

## Install the tools

| Tool | Version | Used by |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | ≥ 0.13.7 | all tasks; installs Python 3.14 when needed |
| [Buf CLI](https://buf.build/docs/cli/installation/) | see `buf.version` in `deploy/platform/versions.yaml` | `proto`, `check` |
| [Helm](https://helm.sh/docs/intro/install/) | 4.x | `check`, `dev`, `integration`, `kind`, `e2e` |
| [Docker](https://docs.docker.com/get-docker/) with Compose v2 and Buildx | recent | `dev`, `integration`, `images`, `kind`, `e2e` |
| [Dapr CLI](https://docs.dapr.io/getting-started/install-dapr-cli/) | see `dapr.runtimeVersion` | `dev`, `integration` |
| [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation), [kubectl](https://kubernetes.io/docs/tasks/tools/) | recent | `kind`, `e2e` |

Install the Dapr runtime used by native sidecars once:

```bash
dapr init --slim --runtime-version 1.19.1
```

`--slim` installs the Dapr binaries (`daprd`, Placement, Scheduler) without starting any service or
container; native sidecars run without Placement and Scheduler. Infrastructure such as Redis
is described in `deploy/environment/infra.yaml` and run by the tasks of this repository.

## Set up the workspace

```bash
uv run --only-group codegen poe setup
```

This generates the Python code for `proto/` into `gen/python/src/` and installs every workspace
member and development tool from `uv.lock`. The first command is special because the generated
package must exist before the full workspace can be installed; afterwards use `uv run poe <task>`.

Run `uv run poe proto` again after changing `.proto` files.

## Run the apps locally

```bash
uv run poe dev
```

`dev` starts the infrastructure instances enabled for `dev` in `deploy/environment/infra.yaml`
(Redis and Jaeger in the example) with Docker, renders the Dapr resources, and runs every app in
`deploy/apps/` with its own sidecar (`dapr run`). Each HTTP app restarts when the source of the app
or of a workspace member it depends on changes; gRPC apps (`counter-grpc`) do not, restart the
session for them. When the apps are ready it prints
`ready: counter-grpc, counter, gateway. Press Ctrl+C to stop.`

Ports declared under `expose` are on the same host ports in `dev` and `kind`. In the example:

```bash
curl -X POST localhost:8080/greet -H 'content-type: application/json' -d '{"name":"Alice"}'
# The same call to the counter over gRPC (the counter-grpc app); the visit count is shared
curl -X POST 'localhost:8080/greet?protocol=grpc' -H 'content-type: application/json' -d '{"name":"Alice"}'
open http://localhost:16686   # Jaeger
```

Apps listen on `127.0.0.1` only, so other machines cannot reach them around Dapr. Press Ctrl+C to
stop; the containers and processes of the session are removed. Closing the terminal or sending
SIGTERM does the same. If an app or sidecar exits on its own, the session stops and `dev` exits
with a non-zero status. Use `--apps gateway` to run a subset. If a declared host port is taken,
change it under `expose`.

## Run on Kubernetes

```bash
uv run poe kind up
curl -X POST localhost:8080/greet -H 'content-type: application/json' -d '{"name":"Alice"}'
```

`kind up` builds the images, creates a cluster named after the project (`dapr-py-monorepo`) or
reuses it, installs the
Dapr control plane, the infrastructure instances enabled for `kind`, the Dapr resources and the
apps. Exposed ports are mapped to their declared host ports; the mapping is also written to
`.run/<run-id>/endpoints.json`. Run it again after a change to rebuild and upgrade. A cluster keeps
the port mappings it was created with: after changing `expose`, run `uv run poe kind down` first.
`kind down` deletes the cluster and the images `kind up` built for it.

## Run the checks

```bash
uv run poe check              # lint + unit tests
uv run poe integration        # integration tests with real sidecars
uv run poe e2e                # BDD tests on a fresh kind cluster
```

See [Testing](testing.md) for details.

## When something is left behind

Every task that creates containers, processes, images or clusters records them in
`.run/<run-id>/environment.json`. `dev`, `integration` and `e2e` clean up when they end, including
on Ctrl+C, SIGTERM and SIGHUP; `kind up` keeps its cluster until `kind down`. If a run was killed
with SIGKILL, or the machine stopped, clean up with:

```bash
uv run poe teardown --run-id <run-id>
```

Teardown leaves a recorded process alone when its pid now belongs to another program (it compares
start times). When the recorded process has already exited, the remaining members of its process
group are still stopped, so run teardown soon after the interruption rather than days later. Logs and reports of each run stay in `.run/<run-id>/`.

## Troubleshooting

- **`Dapr runtime 1.19.1 is required`**: run the `dapr init --slim --runtime-version` command
  printed by the task.
- **An app exits with `component 'statestore' is not loaded`**: the app's startup check did not
  find a required component; check the sidecar log above the error and the rendered files in
  `.run/<run-id>/resources/`.
- **Proxies**: local traffic bypasses proxies set in the shell (`HTTP_PROXY`, `ALL_PROXY`, ...):
  native processes get the loopback addresses in `NO_PROXY`, and every app adds its sidecar's host
  at startup. Other traffic, such as package downloads, still uses the proxies.
- **Service invocation fails with connection errors on a machine with a VPN**: the tasks set
  `DAPR_HOST_IP=127.0.0.1` for native sidecars so that service discovery uses loopback addresses.
  Use the same variable when running `dapr run` by hand.
