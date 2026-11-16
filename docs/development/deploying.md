# Deploying to a cluster

`poe kind up` deploys everything to a local cluster, including infrastructure. A real cluster is
different: the platform provides the Dapr control plane and the infrastructure, and the repository
provides images, the Dapr resources of the system and one release per app. This guide lists the
steps with the commands to run; adapt them to your delivery pipeline.

## What the platform provides

| Item | Notes |
| --- | --- |
| Dapr control plane | the version in `deploy/platform/versions.yaml` or a compatible one, installed from the official chart |
| Infrastructure | the services your components use (state stores, brokers, databases), and credentials as Kubernetes Secrets |
| Trace collector (optional) | an OTLP endpoint for apps and sidecars |
| Image registry | where the images are pushed and pulled from |
| Ingress | how exposed apps are reached from outside the cluster |

## 1. Build and push the images

```bash
uv run poe images \
  --prefix registry.example.com/team \
  --tag 1.4.0 \
  --platform linux/amd64,linux/arm64 \
  --push
```

Every image target (`docker-bake.hcl`) is built for each platform, together with the shared
wheelhouse, and pushed as `registry.example.com/team/<app>:1.4.0`. The references are written to
`.run/images/images.json`. The pushing machine needs `docker login` to the registry.

## 2. Describe where the infrastructure is

Write the environment's `resources-values.yaml`, keyed by the resource names your components use
(`resource:` in `deploy/environment/components.yaml`). The format of each kind is in
`deploy/platform/resources-values.example.yaml`:

```yaml
resources:
  redis:
    kind: redis
    host: redis.data.svc.cluster.local
    port: 6379
    passwordSecret:
      name: redis-credentials
      key: password
  tracing:                        # optional: sidecars export spans here
    kind: otlp
    endpoint: otel-collector.observability.svc:4317
    insecure: false
```

## 3. Install the Dapr resources

```bash
helm upgrade --install dapr-resources deploy/platform/charts/dapr-resources \
  --namespace my-system --create-namespace \
  --values deploy/environment/components.yaml \
  --values resources-values.yaml
```

This creates the Components, with their scopes, and the `app-config` Configuration in the
namespace. Rendering fails if a component refers to a resource the file does not provide. Skip this
step if the platform manages Dapr resources itself; the apps only need the component names and the
Configuration they reference.

## 4. Install the apps

One release per file in `deploy/apps/`, with the environment's overrides:

```yaml
# gateway-production.yaml
image:
  repository: registry.example.com/team/gateway
  tag: "1.4.0"
replicas: 2
env:                                                # merged with the env of deploy/apps/gateway.yaml
  OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector.observability.svc:4318
  OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf
service:
  enabled: true                                     # for apps with `expose`; route your Ingress here
  type: ClusterIP
resources:
  requests: {cpu: 100m, memory: 128Mi}
  limits: {memory: 512Mi}
```

```bash
helm upgrade --install gateway deploy/platform/charts/app \
  --namespace my-system \
  --values deploy/apps/gateway.yaml \
  --values gateway-production.yaml \
  --wait
```

- Helm merges the two values files key by key: `env` adds to and overrides the settings in the
  deployment input.
- Only apps that are called from outside the mesh need `service.enabled`; the others are reached
  through Dapr service invocation.
- `--wait` returns when the Pods are ready, which means each app's Dapr startup checks passed. An
  app whose required components are missing stays unready and logs the missing components.

## 5. Check

```bash
kubectl --namespace my-system get pods          # 2/2 ready: app and daprd
kubectl --namespace my-system logs deploy/gateway -c app
```

## Not covered by the template

Ingress, Secret management, autoscaling, Dapr access control policies, Dapr API tokens and
rollouts are environment decisions. The charts accept the usual values (`replicas`, `resources`,
`env`, image settings); extend `deploy/platform/charts/app` when your environment needs more.
