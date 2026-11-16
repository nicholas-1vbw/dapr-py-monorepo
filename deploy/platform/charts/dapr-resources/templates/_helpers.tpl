{{/* Look up a backing service by alias; fail when it is not provided. */}}
{{- define "dapr-resources.resource" -}}
{{- $root := index . 0 -}}
{{- $alias := index . 1 -}}
{{- $kind := index . 2 -}}
{{- $resource := index $root.Values.resources $alias -}}
{{- if not $resource -}}
{{- fail (printf "resources.%s is not provided (expected kind %q in resources-values.yaml)" $alias $kind) -}}
{{- end -}}
{{- if ne $resource.kind $kind -}}
{{- fail (printf "resources.%s has kind %q, expected %q" $alias $resource.kind $kind) -}}
{{- end -}}
{{- toYaml $resource -}}
{{- end }}
