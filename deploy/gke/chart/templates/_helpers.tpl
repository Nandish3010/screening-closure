{{- define "slt.labels" -}}
{{ .Values.label }}: "true"
sc-loadtest-run: {{ .Values.runId | quote }}
app.kubernetes.io/part-of: screening-closure-loadtest
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
