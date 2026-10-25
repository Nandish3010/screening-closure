// One user-assigned managed identity for the container apps: reads Key Vault secrets (role assigned in keyvault.bicep) and,
// once you grant it AcrPull on your registry, pulls the images. No credentials are stored anywhere.
param location string
param prefix string
param tags object

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-apps-id'
  location: location
  tags: tags
}

output id string = appIdentity.id
output principalId string = appIdentity.properties.principalId
output clientId string = appIdentity.properties.clientId
