{{- define "app.labels" -}}
app.kubernetes.io/name: {{ .Values.appId }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "app.selector" -}}
app.kubernetes.io/name: {{ .Values.appId }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Probe of the app port: an HTTP GET of `path`, or for a gRPC app whether its
port accepts connections. Arguments: (list . path). */}}
{{- define "app.probe" -}}
{{- $root := index . 0 -}}
{{- if eq $root.Values.appProtocol "grpc" -}}
tcpSocket:
  port: http
{{- else -}}
httpGet:
  path: {{ index . 1 }}
  port: http
{{- end }}
{{- end }}
