// Log Analytics workspace in the same (in-country) region: container logs land here, never outside UAE North.
param location string
param prefix string
param tags object

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90
    features: {
      disableLocalAuth: true
    }
  }
}

output workspaceId string = workspace.id
output customerId string = workspace.properties.customerId
