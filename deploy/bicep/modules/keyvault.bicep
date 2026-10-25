// Key Vault: RBAC authorisation, soft delete and purge protection, no public network access, a private endpoint in the VNet.
param location string
param prefix string
param tags object
param privateEndpointSubnetId string
param vaultDnsZoneId string
param appIdentityPrincipalId string
param adminObjectIds array
@secure()
param postgresAdminPassword string

var vaultName = take('${replace(prefix, '-', '')}kv${uniqueString(resourceGroup().id)}', 24)
// Built-in role ids: Key Vault Secrets User, Key Vault Secrets Officer.
var kvUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var kvOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${vaultName}-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'vault'
        properties: {
          privateLinkServiceId: vault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: { privateDnsZoneId: vaultDnsZoneId }
      }
    ]
  }
}

resource appReadsSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, appIdentityPrincipalId, kvUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvUserRoleId)
    principalId: appIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource adminsManageSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for objectId in adminObjectIds: {
  scope: vault
  name: guid(vault.id, objectId, kvOfficerRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvOfficerRoleId)
    principalId: objectId
  }
}]

// The only secret this template writes is the one it was handed: the database administrator password.
resource postgresPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'postgres-admin-password'
  properties: {
    value: postgresAdminPassword
    attributes: { enabled: true }
  }
  dependsOn: [privateEndpoint]
}

output vaultName string = vault.name
output vaultUri string = vault.properties.vaultUri
