{{- define "app.labels" -}}
app.kubernetes.io/name: {{ .Values.appId }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "app.selector" -}}
app.kubernetes.io/name: {{ .Values.appId }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
