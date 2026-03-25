@description('Azure region for resources')
param location string

@description('Container Apps Environment name')
param environmentName string

@description('Frontend Container App name')
param frontendContainerAppName string

@description('MCP Memory Container App name')
param mcpContainerAppName string

@description('Container Registry name')
param registryName string

@description('Entra ID client ID for the web app')
param entraClientId string

@description('Entra ID tenant ID')
param entraTenantId string

@description('Azure OpenAI endpoint (https://{name}.openai.azure.com/)')
param openaiEndpoint string

@description('GPT deployment name')
param gptDeploymentName string

@description('GPT mini deployment name (for memory extraction)')
param gptMiniDeploymentName string

@description('Embedding deployment name')
param embeddingDeploymentName string

@description('Cosmos DB endpoint')
param cosmosEndpoint string

@description('Cosmos DB database name')
param cosmosDatabaseName string

@description('Cosmos DB container name')
param cosmosContainerName string

@description('Backend Container App name')
param backendContainerAppName string

@description('MCP Search Container App name')
param mcpSearchContainerAppName string

@description('Azure AI Search endpoint')
param aiSearchEndpoint string

@description('Azure AI Search index name')
param aiSearchIndexName string = 'knowledge-index'

@description('Microsoft Foundry project endpoint for agent operations')
param aiProjectEndpoint string

@description('Embedding vector dimensions')
param embeddingDimensions int

@description('Tags for all resources')
param tags object = {}

@description('Tags for the MCP memory container app')
param mcpTags object = {}

@description('Tags for the MCP search container app')
param mcpSearchTags object = {}

@description('Tags for the backend container app')
param backendTags object = {}

// ---------------------------------------------------------------------------
// Log Analytics Workspace (required by Container Apps Environment)
// ---------------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${environmentName}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------------------
// Container Apps Environment
// ---------------------------------------------------------------------------

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container Registry
// ---------------------------------------------------------------------------

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

// ---------------------------------------------------------------------------
// Container App (Web Frontend)
// ---------------------------------------------------------------------------

// Combine registry secret with any additional secrets
var allSecrets = [
  {
    name: 'registry-password'
    value: containerRegistry.listCredentials().passwords[0].value
  }
]

// ---------------------------------------------------------------------------
// MCP Memory Container App
// ---------------------------------------------------------------------------

resource mcpContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: mcpContainerAppName
  location: location
  tags: mcpTags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          username: containerRegistry.listCredentials().username
          passwordSecretRef: 'registry-password'
        }
      ]
      secrets: allSecrets
    }
    template: {
      containers: [
        {
          name: 'main'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          env: [
            {
              name: 'FOUNDRY_ENDPOINT'
              value: openaiEndpoint
            }
            {
              name: 'FOUNDRY_EMBEDDING_DEPLOYMENT'
              value: embeddingDeploymentName
            }
            {
              name: 'FOUNDRY_MEMORY_MODEL_DEPLOYMENT'
              value: gptMiniDeploymentName
            }
            {
              name: 'COSMOS_ENDPOINT'
              value: cosmosEndpoint
            }
            {
              name: 'COSMOS_DATABASE'
              value: cosmosDatabaseName
            }
            {
              name: 'COSMOS_CONTAINER'
              value: cosmosContainerName
            }
            {
              name: 'EMBEDDING_DIMENSIONS'
              value: string(embeddingDimensions)
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
      }
    }
  }
}

// ---------------------------------------------------------------------------
// MCP Search Container App
// ---------------------------------------------------------------------------

resource mcpSearchContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: mcpSearchContainerAppName
  location: location
  tags: mcpSearchTags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          username: containerRegistry.listCredentials().username
          passwordSecretRef: 'registry-password'
        }
      ]
      secrets: allSecrets
    }
    template: {
      containers: [
        {
          name: 'main'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          env: [
            {
              name: 'AZURE_AI_SEARCH_ENDPOINT'
              value: aiSearchEndpoint
            }
            {
              name: 'INDEX_NAME'
              value: aiSearchIndexName
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Backend Container App
// ---------------------------------------------------------------------------

resource backendContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: backendContainerAppName
  location: location
  tags: backendTags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          username: containerRegistry.listCredentials().username
          passwordSecretRef: 'registry-password'
        }
      ]
      secrets: allSecrets
    }
    template: {
      containers: [
        {
          name: 'main'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          env: [
            {
              name: 'FOUNDRY_CHAT_MODEL_DEPLOYMENT'
              value: gptDeploymentName
            }
            {
              name: 'MCP_MEMORY_ENDPOINT'
              value: 'https://${mcpContainerApp.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'MCP_SEARCH_ENDPOINT'
              value: 'https://${mcpSearchContainerApp.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'FOUNDRY_PROJECT_ENDPOINT'
              value: aiProjectEndpoint
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/ready'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 12
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Web Frontend Container App (after backend so we can reference its URL)
// ---------------------------------------------------------------------------

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: frontendContainerAppName
  location: location
  tags: tags
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          username: containerRegistry.listCredentials().username
          passwordSecretRef: 'registry-password'
        }
      ]
      secrets: allSecrets
    }
    template: {
      containers: [
        {
          name: 'main'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          env: [
            {
              name: 'ENTRA_CLIENT_ID'
              value: entraClientId
            }
            {
              name: 'ENTRA_TENANT_ID'
              value: entraTenantId
            }
            {
              name: 'BACKEND_URL'
              value: 'https://${backendContainerApp.properties.configuration.ingress.fqdn}'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output fqdn string = containerApp.properties.configuration.ingress.fqdn
output frontendContainerAppName string = containerApp.name
output registryLoginServer string = containerRegistry.properties.loginServer
output mcpContainerAppName string = mcpContainerApp.name
output mcpFqdn string = mcpContainerApp.properties.configuration.ingress.fqdn
output mcpUri string = 'https://${mcpContainerApp.properties.configuration.ingress.fqdn}'
output mcpPrincipalId string = mcpContainerApp.identity.principalId
output mcpSearchContainerAppName string = mcpSearchContainerApp.name
output mcpSearchFqdn string = mcpSearchContainerApp.properties.configuration.ingress.fqdn
output mcpSearchUri string = 'https://${mcpSearchContainerApp.properties.configuration.ingress.fqdn}'
output mcpSearchPrincipalId string = mcpSearchContainerApp.identity.principalId
output backendContainerAppName string = backendContainerApp.name
output backendFqdn string = backendContainerApp.properties.configuration.ingress.fqdn
output backendUri string = 'https://${backendContainerApp.properties.configuration.ingress.fqdn}'
output backendPrincipalId string = backendContainerApp.identity.principalId
