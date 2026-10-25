{{- define "sc.labels" -}}
app.kubernetes.io/part-of: screening-closure
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "sc.secretName" -}}
{{- if .Values.secrets.create -}}{{ .Release.Name }}-secrets{{- else -}}{{ .Values.secrets.existingSecret }}{{- end -}}
{{- end -}}

{{/* Pod-level settings shared by every workload. */}}
{{- define "sc.podCommon" -}}
automountServiceAccountToken: false
{{- with .Values.imagePullSecrets }}
imagePullSecrets: {{ toYaml . | nindent 2 }}
{{- end }}
securityContext: {{ toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.nodeSelector }}
nodeSelector: {{ toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations: {{ toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* One secret key as an env var. Call with (dict "key" K "secret" S). */}}
{{- define "sc.secretEnv" -}}
- name: {{ .key }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ .key }}
{{- end -}}

{{/* The API's environment. */}}
{{- define "sc.apiEnv" -}}
- { name: APP_ENV, value: {{ .Values.app.env | quote }} }
- { name: AUTH_MODE, value: oidc }
- { name: DEV_LOGIN_ENABLED, value: "false" }
- { name: CLOCK_MODE, value: wall }
- { name: OUTREACH_DEMO_APPROVAL, value: "0" }
- { name: OIDC_ISSUER, value: {{ .Values.app.oidcIssuer | quote }} }
- { name: OIDC_AUDIENCE, value: {{ .Values.app.oidcAudience | quote }} }
- { name: OIDC_JWKS_URL, value: {{ .Values.app.oidcJwksUrl | quote }} }
- { name: OIDC_ROLE_CLAIM, value: {{ .Values.app.oidcRoleClaim | quote }} }
- { name: OIDC_ROLE_MAP, value: {{ .Values.app.oidcRoleMap | quote }} }
- { name: PATHWAY_PATH, value: /config/pathways/colorectal_fit.yaml }
- { name: CALENDAR_PATH, value: /config/calendar.yaml }
- { name: PROVIDERS_PATH, value: /config/providers.yaml }
- { name: OUTREACH_POLICY_PATH, value: /config/outreach_policy.yaml }
- { name: AGENCY_CONFIG_PATH, value: /config/agency.yaml }
- { name: FHIR_BASE_URL, value: {{ .Values.app.fhirBaseUrl | quote }} }
- { name: FHIR_VERIFY_EVIDENCE, value: {{ .Values.app.fhirVerifyEvidence | quote }} }
- { name: FHIR_PROJECTION, value: {{ .Values.app.fhirProjection | quote }} }
- { name: AUTOMATION_ENABLED, value: {{ .Values.app.automationEnabled | quote }} }
- { name: LEADER_LEASE_TTL_SECONDS, value: {{ .Values.app.leaderLeaseTtlSeconds | quote }} }
- { name: REQUIRE_LEADER_FENCE, value: {{ .Values.app.requireLeaderFence | quote }} }
- { name: SWEEP_WORKERS, value: {{ .Values.api.sweepWorkers | quote }} }
- { name: CORS_ALLOW_ORIGINS, value: {{ .Values.app.corsAllowOrigins | quote }} }
- { name: LLM_PROVIDER, value: {{ .Values.app.llmProvider | quote }} }
- { name: LOG_FORMAT, value: {{ .Values.app.logFormat | quote }} }
- { name: OTEL_EXPORTER_OTLP_ENDPOINT, value: {{ .Values.app.otlpEndpoint | quote }} }
- { name: HOSTNAME, valueFrom: { fieldRef: { fieldPath: metadata.name } } }
{{- $s := include "sc.secretName" . }}
{{- range $k := list "DATABASE_URL" "DEV_AUTH_SECRET" "SCHEDULER_SERVICE_TOKEN" "WATCHDOG_SERVICE_TOKEN" "AUTOMATION_SERVICE_TOKEN" "AGENCY_SERVICE_TOKEN" "METRICS_SCRAPE_TOKEN" }}
{{ include "sc.secretEnv" (dict "key" $k "secret" $s) }}
{{- end }}
{{- end -}}

{{- define "sc.configVolume" -}}
- name: config
  configMap:
    name: {{ .Values.config.existingConfigMap }}
    items:
{{- range .Values.config.items }}
      - { key: {{ .key }}, path: {{ .path }} }
{{- end }}
{{- end -}}
