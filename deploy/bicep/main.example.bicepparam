// Example parameters. Copy to main.bicepparam (git-ignored) and edit. Placeholders only; the password comes from the pipeline:
//   az deployment group create -g <rg> -f main.bicep -p main.bicepparam -p postgresAdminPassword="$PG_ADMIN_PASSWORD"
using 'main.bicep'

param location = 'uaenorth'
param namePrefix = 'sclosure'
param environmentName = 'test'
param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD', 'CHANGE_ME_NOT_A_SECRET')
param postgresHighAvailability = 'Disabled'
param keyVaultAdminObjectIds = []
param deployApps = false
param containerRegistryServer = 'REGISTRY.azurecr.io'
param oidcIssuer = 'https://idp.example.invalid/realms/placeholder'
param fhirBaseUrl = 'https://fhir.example.invalid/fhir'
