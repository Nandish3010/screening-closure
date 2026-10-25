// Virtual network, three subnets, and the private DNS zones for PostgreSQL and Key Vault. No public IP is created here.
param location string
param prefix string
param tags object
param vnetAddressPrefix string
param appsSubnetPrefix string
param dbSubnetPrefix string
param privateEndpointSubnetPrefix string

var postgresZoneName = '${prefix}.private.postgres.database.azure.com'
var vaultZoneName = 'privatelink.vaultcore.azure.net'

resource nsgApps 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: '${prefix}-apps-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: []
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: '${prefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: 'apps'
        properties: {
          addressPrefix: appsSubnetPrefix
          networkSecurityGroup: { id: nsgApps.id }
          delegations: [
            {
              name: 'containerapps'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
      {
        name: 'db'
        properties: {
          addressPrefix: dbSubnetPrefix
          delegations: [
            {
              name: 'postgres'
              properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

resource postgresZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: postgresZoneName
  location: 'global'
  tags: tags
}

resource vaultZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: vaultZoneName
  location: 'global'
  tags: tags
}

resource postgresLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: postgresZone
  name: '${prefix}-postgres-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

resource vaultLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: vaultZone
  name: '${prefix}-vault-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

output appsSubnetId string = '${vnet.id}/subnets/apps'
output dbSubnetId string = '${vnet.id}/subnets/db'
output privateEndpointSubnetId string = '${vnet.id}/subnets/private-endpoints'
output postgresDnsZoneId string = postgresZone.id
output vaultDnsZoneId string = vaultZone.id
