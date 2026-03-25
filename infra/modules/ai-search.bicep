@description('Azure region for the AI Search service')
param location string

@description('Name of the AI Search service (must be globally unique)')
param name string

@description('SKU for the AI Search service')
@allowed(['free', 'basic', 'standard'])
param skuName string = 'basic'

@description('Tags for the AI Search service')
param tags object = {}

// ---------------------------------------------------------------------------
// Azure AI Search Service
// ---------------------------------------------------------------------------

resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hostingMode: 'default'
    partitionCount: 1
    replicaCount: 1
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output endpoint string = 'https://${searchService.name}.search.windows.net'
output name string = searchService.name
output principalId string = searchService.identity.principalId
