// Screening closure engine on Azure, UAE North (F14). Resource-group scope. Every name, size and address is a parameter;
// no secret is in this file (the Postgres administrator password is a @secure parameter you supply at deploy time, the
// application secrets are created in Key Vault by you, see docs/governance/deployment.md).
//
// Shape: private virtual network -> Container Apps environment (internal, no public ingress) running api (2+ replicas),
// scheduler (2, one leads), watchdog and the migration job; PostgreSQL Flexible Server with private access only and
// in-country backups; Key Vault with a private endpoint, no public access, purge protection. Why Bicep and Container Apps
// rather than Terraform and AKS: docs/governance/deployment.md section "Choices".
//
// Deploy in two steps: `deployApps=false` creates the network, vault, database and environment; you then put the secrets in the
// vault; `deployApps=true` creates the apps, which read those secrets through a managed identity.
targetScope = 'resourceGroup'

@description('Azure region. UAE North keeps data in the country (DoH data-residency rule); do not change without the regulator.')
@allowed(['uaenorth'])
param location string = 'uaenorth'

@description('Short prefix for resource names, 3 to 11 lowercase letters and digits.')
@minLength(3)
@maxLength(11)
param namePrefix string = 'sclosure'

@description('Environment label used in names and tags.')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'prod'

@description('Address space of the virtual network.')
param vnetAddressPrefix string = '10.20.0.0/16'
@description('Container Apps subnet (needs /23 or larger).')
param appsSubnetPrefix string = '10.20.0.0/23'
@description('PostgreSQL delegated subnet.')
param dbSubnetPrefix string = '10.20.2.0/24'
@description('Private endpoint subnet.')
param privateEndpointSubnetPrefix string = '10.20.4.0/24'

@description('PostgreSQL SKU name, e.g. Standard_D4ds_v5.')
param postgresSkuName string = 'Standard_D4ds_v5'
@description('PostgreSQL SKU tier.')
@allowed(['GeneralPurpose', 'MemoryOptimized', 'Burstable'])
param postgresSkuTier string = 'GeneralPurpose'
@description('Storage in GiB (auto-grow is on).')
param postgresStorageGb int = 128
@description('Zone-redundant high availability (prod).')
@allowed(['Disabled', 'ZoneRedundant'])
param postgresHighAvailability string = 'ZoneRedundant'
@description('Backup retention in days (7 to 35). Point-in-time restore reaches back this far; see the erasure caveat in deployment.md.')
@minValue(7)
@maxValue(35)
param postgresBackupRetentionDays int = 35
@description('Administrator login of the server (the schema owner). Not the application role.')
param postgresAdminLogin string = 'closure_owner'
@secure()
@description('Administrator password. Supplied by the pipeline at deploy time; stored in Key Vault as postgres-admin-password.')
param postgresAdminPassword string

@description('Object ids allowed to administer Key Vault secrets (a group, not a person where possible).')
param keyVaultAdminObjectIds array = []

@description('Create the container apps and the migration job. false on the first run (the vault has no secrets yet).')
param deployApps bool = false

@description('Container registry login server, e.g. myregistry.azurecr.io (placeholder). The identity is granted AcrPull on it by you.')
param containerRegistryServer string = 'REGISTRY.azurecr.io'
param imageTag string = '0.1.0'
param apiReplicasMin int = 2
param apiReplicasMax int = 6
param apiCpu string = '1.0'
param apiMemory string = '2Gi'

@description('OIDC issuer (https) of the identity provider, UAE PASS or the organisation\'s own.')
param oidcIssuer string = 'https://idp.example.invalid/realms/placeholder'
@description('Base URL of the FHIR server the host approves.')
param fhirBaseUrl string = 'https://fhir.example.invalid/fhir'
@description('OTLP/HTTP endpoint of the OpenTelemetry collector; empty exports nothing.')
param otlpEndpoint string = ''
@description('Web app origin(s) allowed by CORS, comma separated.')
param corsAllowOrigins string = ''

param tags object = {
  application: 'screening-closure'
  environment: environmentName
  dataClassification: 'health-information'
}

var prefix = '${namePrefix}-${environmentName}'

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    prefix: prefix
    tags: tags
    vnetAddressPrefix: vnetAddressPrefix
    appsSubnetPrefix: appsSubnetPrefix
    dbSubnetPrefix: dbSubnetPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    prefix: prefix
    tags: tags
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    location: location
    prefix: prefix
    tags: tags
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    prefix: prefix
    tags: tags
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    vaultDnsZoneId: network.outputs.vaultDnsZoneId
    appIdentityPrincipalId: identity.outputs.principalId
    adminObjectIds: keyVaultAdminObjectIds
    postgresAdminPassword: postgresAdminPassword
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    prefix: prefix
    tags: tags
    delegatedSubnetId: network.outputs.dbSubnetId
    privateDnsZoneId: network.outputs.postgresDnsZoneId
    skuName: postgresSkuName
    skuTier: postgresSkuTier
    storageGb: postgresStorageGb
    highAvailability: postgresHighAvailability
    backupRetentionDays: postgresBackupRetentionDays
    adminLogin: postgresAdminLogin
    adminPassword: postgresAdminPassword
  }
}

module apps 'modules/apps.bicep' = {
  name: 'apps'
  params: {
    location: location
    prefix: prefix
    tags: tags
    appsSubnetId: network.outputs.appsSubnetId
    logAnalyticsCustomerId: monitoring.outputs.customerId
    logAnalyticsId: monitoring.outputs.workspaceId
    identityId: identity.outputs.id
    keyVaultUri: keyvault.outputs.vaultUri
    deployApps: deployApps
    registryServer: containerRegistryServer
    imageTag: imageTag
    apiReplicasMin: apiReplicasMin
    apiReplicasMax: apiReplicasMax
    apiCpu: apiCpu
    apiMemory: apiMemory
    oidcIssuer: oidcIssuer
    fhirBaseUrl: fhirBaseUrl
    otlpEndpoint: otlpEndpoint
    corsAllowOrigins: corsAllowOrigins
  }
}

output postgresFqdn string = postgres.outputs.fqdn
output keyVaultName string = keyvault.outputs.vaultName
output appsEnvironmentDefaultDomain string = apps.outputs.defaultDomain
output appIdentityClientId string = identity.outputs.clientId
