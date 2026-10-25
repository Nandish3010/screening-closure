// Container Apps environment (VNet-integrated, internal: no public ingress) and, when deployApps is true, the api, scheduler,
// watchdog and the migration job. Secrets are Key Vault references resolved by the managed identity; none is a template value.
param location string
param prefix string
param tags object
param appsSubnetId string
param logAnalyticsCustomerId string
param logAnalyticsId string
param identityId string
param keyVaultUri string
param deployApps bool
param registryServer string
param imageTag string
param apiReplicasMin int
param apiReplicasMax int
param apiCpu string
param apiMemory string
param oidcIssuer string
param fhirBaseUrl string
param otlpEndpoint string
param corsAllowOrigins string

// Key Vault secret names the apps read (create them before deployApps=true; deployment.md lists how).
var secretNames = [
  'database-url'
  'migrate-database-url'
  'app-db-password'
  'dev-auth-secret'
  'scheduler-service-token'
  'watchdog-service-token'
  'automation-service-token'
  'agency-service-token'
  'metrics-scrape-token'
]

var vaultSecrets = [for name in secretNames: {
  name: name
  keyVaultUrl: '${keyVaultUri}secrets/${name}'
  identity: identityId
}]


// Environment variables whose values come from the Key Vault references above (name of the variable, name of the reference).
var apiSecretEnv = [for e in [
  { name: 'DATABASE_URL', ref: 'database-url' }
  { name: 'DEV_AUTH_SECRET', ref: 'dev-auth-secret' }
  { name: 'SCHEDULER_SERVICE_TOKEN', ref: 'scheduler-service-token' }
  { name: 'WATCHDOG_SERVICE_TOKEN', ref: 'watchdog-service-token' }
  { name: 'AUTOMATION_SERVICE_TOKEN', ref: 'automation-service-token' }
  { name: 'AGENCY_SERVICE_TOKEN', ref: 'agency-service-token' }
  { name: 'METRICS_SCRAPE_TOKEN', ref: 'metrics-scrape-token' }
]: { name: e.name, secretRef: e.ref }]
var schedulerSecretEnv = [for e in [
  { name: 'SCHEDULER_SERVICE_TOKEN', ref: 'scheduler-service-token' }
]: { name: e.name, secretRef: e.ref }]
var watchdogSecretEnv = [for e in [
  { name: 'WATCHDOG_SERVICE_TOKEN', ref: 'watchdog-service-token' }
]: { name: e.name, secretRef: e.ref }]
var migrateSecretEnv = [for e in [
  { name: 'DATABASE_URL', ref: 'migrate-database-url' }
  { name: 'APP_DB_PASSWORD', ref: 'app-db-password' }
]: { name: e.name, secretRef: e.ref }]

var registries = [
  {
    server: registryServer
    identity: identityId
  }
]

var identityBlock = {
  type: 'UserAssigned'
  userAssignedIdentities: {
    '${identityId}': {}
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: appsSubnetId
      internal: true
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: listKeys(logAnalyticsId, '2023-09-01').primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

resource api 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: '${prefix}-api'
  location: location
  tags: tags
  identity: identityBlock
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: registries
      secrets: vaultSecrets
      ingress: {
        external: false
        targetPort: 8000
        transport: 'http'
        allowInsecure: true // inside the environment only; TLS terminates at the gateway in front of it
      }
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${registryServer}/screening-closure-api:${imageTag}'
          resources: { cpu: json(apiCpu), memory: apiMemory }
          env: concat([
            { name: 'APP_ENV', value: 'production' }
            { name: 'AUTH_MODE', value: 'oidc' }
            { name: 'DEV_LOGIN_ENABLED', value: 'false' }
            { name: 'CLOCK_MODE', value: 'wall' }
            { name: 'OUTREACH_DEMO_APPROVAL', value: '0' }
            { name: 'OIDC_ISSUER', value: oidcIssuer }
            { name: 'FHIR_BASE_URL', value: fhirBaseUrl }
            { name: 'FHIR_VERIFY_EVIDENCE', value: 'true' }
            { name: 'FHIR_PROJECTION', value: 'true' }
            { name: 'AUTOMATION_ENABLED', value: 'true' }
            { name: 'REQUIRE_LEADER_FENCE', value: 'true' }
            { name: 'LOG_FORMAT', value: 'json' }
            { name: 'CORS_ALLOW_ORIGINS', value: corsAllowOrigins }
            { name: 'OTEL_EXPORTER_OTLP_ENDPOINT', value: otlpEndpoint }
          ], apiSecretEnv)
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 5
              failureThreshold: 36
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 10
              failureThreshold: 6
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: apiReplicasMin
        maxReplicas: apiReplicasMax
        rules: [
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
}

// Exactly two scheduler instances: one leads, the other takes over within the lease TTL.
resource scheduler 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: '${prefix}-scheduler'
  location: location
  tags: tags
  identity: identityBlock
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: registries
      secrets: vaultSecrets
    }
    template: {
      containers: [
        {
          name: 'scheduler'
          image: '${registryServer}/screening-closure-scheduler:${imageTag}'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: concat([
            { name: 'API_URL', value: 'http://${prefix}-api' }
            { name: 'HEARTBEAT_PATH', value: '/tmp/scheduler.json' }
          ], schedulerSecretEnv)
        }
      ]
      scale: { minReplicas: 2, maxReplicas: 2 }
    }
  }
}

resource watchdog 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: '${prefix}-watchdog'
  location: location
  tags: tags
  identity: identityBlock
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: registries
      secrets: vaultSecrets
    }
    template: {
      containers: [
        {
          name: 'watchdog'
          image: '${registryServer}/screening-closure-watchdog:${imageTag}'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: concat([
            { name: 'API_URL', value: 'http://${prefix}-api' }
          ], watchdogSecretEnv)
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

// Schema migration as the database owner. Started by the pipeline before each new api revision:
//   az containerapp job start -n <prefix>-migrate -g <resource group>
resource migrate 'Microsoft.App/jobs@2024-03-01' = if (deployApps) {
  name: '${prefix}-migrate'
  location: location
  tags: tags
  identity: identityBlock
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 1
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: registries
      secrets: vaultSecrets
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: '${registryServer}/screening-closure-api:${imageTag}'
          command: ['python', '-m', 'app.migrate']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat([
          ], migrateSecretEnv)
        }
      ]
    }
  }
}

output defaultDomain string = environment.properties.defaultDomain
