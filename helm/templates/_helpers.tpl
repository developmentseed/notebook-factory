{{- define "nf.labels" -}}
app.kubernetes.io/name: notebook-factory
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
{{- end }}

{{- define "nf.selector" -}}
app.kubernetes.io/name: notebook-factory
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "nf.redis" -}}{{ .Release.Name }}-redis{{- end }}
