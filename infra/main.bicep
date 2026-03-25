targetScope = 'subscription'

extension microsoftGraphV1_0

@description('Primary location for all resources')
@allowed([
  'australiaeast'
  'eastus'
  'eastus2'
  'japaneast'
  'southindia'
  'swedencentral'
  'switzerlandnorth'
  'uksouth'
])
param location string

@description('Environment name used for resource naming')
param environmentName string

@description('Display name for the Entra ID app registration')
param appDisplayName string = 'Agent Memory Demo'

@description('Redirect URIs for the web application')
param redirectUris array = [
  'http://localhost:5173'
]

@description('Embedding vector dimensions (must match the model output size)')
param embeddingDimensions int = 3072

@description('Azure region for Cosmos DB deployment')
@allowed([
  'australiaeast'
  'brazilsouth'
  'canadacentral'
  'canadaeast'
  'centralindia'
  'centralus'
  'eastasia'
  'eastus'
  'eastus2'
  'francecentral'
  'germanywestcentral'
  'japaneast'
  'koreacentral'
  'northcentralus'
  'northeurope'
  'norwayeast'
  'polandcentral'
  'southafricanorth'
  'southcentralus'
  'southeastasia'
  'swedencentral'
  'switzerlandnorth'
  'uaenorth'
  'uksouth'
  'westcentralus'
  'westeurope'
  'westus'
  'westus2'
  'westus3'
])
param cosmosDbLocation string

@description('Azure region for AI Search deployment')
@allowed([
  // Americas
  'brazilsouth'
  'canadacentral'
  'canadaeast'
  'centralus'
  'eastus'
  'eastus2'
  'mexicocentral'
  'northcentralus'
  'southcentralus'
  'westcentralus'
  'westus'
  'westus2'
  'westus3'
  // Europe
  'francecentral'
  'germanywestcentral'
  'italynorth'
  'northeurope'
  'norwayeast'
  'polandcentral'
  'spaincentral'
  'swedencentral'
  'switzerlandnorth'
  'switzerlandwest'
  'uksouth'
  'ukwest'
  'westeurope'
  // Middle East
  'israelcentral'
  'qatarcentral'
  'uaenorth'
  // Africa
  'southafricanorth'
  // Asia Pacific
  'australiaeast'
  'australiasoutheast'
  'centralindia'
  'eastasia'
  'indonesiacentral'
  'japaneast'
  'japanwest'
  'jioindiacentral'
  'jioindiawest'
  'koreacentral'
  'koreasouth'
  'malaysiawest'
  'newzealandnorth'
  'southeastasia'
  'southindia'
])
param aiSearchLocation string

@description('Principal ID of the current user for role assignments')
param principalId string = ''

// Resource group name derived from environment name
var resourceGroupName = 'rg-${environmentName}'

// 4-character suffix — consistent across all resources in this resource group
var suffix = substring(uniqueString(subscription().subscriptionId, resourceGroupName), 0, 4)

// ---------------------------------------------------------------------------
// Resource Group
// ---------------------------------------------------------------------------

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

// ---------------------------------------------------------------------------
// Entra ID App Registration (Microsoft Graph Bicep extension)
// ---------------------------------------------------------------------------

resource appRegistration 'Microsoft.Graph/applications@v1.0' = {
  displayName: appDisplayName
  uniqueName: 'agent-memory-${environmentName}'
  spa: {
    redirectUris: redirectUris
  }
  requiredResourceAccess: [
    {
      resourceAppId: '00000003-0000-0000-c000-000000000000' // Microsoft Graph
      resourceAccess: [
        {
          id: 'e1fe6dd8-ba31-4d61-89e7-88639da4683d' // User.Read
          type: 'Scope'
        }
      ]
    }
  ]
}

resource servicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: appRegistration.appId
}

// ---------------------------------------------------------------------------
// Cosmos DB
// ---------------------------------------------------------------------------

module cosmosDb 'modules/cosmos-db.bicep' = {
  name: 'cosmos-db'
  scope: rg
  params: {
    location: cosmosDbLocation
    accountName: 'cosmos-${environmentName}-${suffix}'
    databaseName: 'agentmemory'
    containerName: 'memories'
    embeddingDimensions: embeddingDimensions
  }
}

// ---------------------------------------------------------------------------
// Microsoft Foundry
// ---------------------------------------------------------------------------

module aiFoundry 'modules/ai-foundry.bicep' = {
  name: 'ai-foundry'
  scope: rg
  params: {
    name: 'aif-${environmentName}-${suffix}'
    location: location
    tags: {
      'azd-env-name': environmentName
    }
  }
}

// ---------------------------------------------------------------------------
// Azure AI Search
// ---------------------------------------------------------------------------

module aiSearch 'modules/ai-search.bicep' = {
  name: 'ai-search'
  scope: rg
  params: {
    name: 'srch-${environmentName}-${suffix}'
    location: aiSearchLocation
    tags: {
      'azd-env-name': environmentName
    }
  }
}

// ---------------------------------------------------------------------------
// Container Apps + Container Registry
// ---------------------------------------------------------------------------

module containerApps 'modules/container-apps.bicep' = {
  name: 'container-apps'
  scope: rg
  params: {
    location: location
    environmentName: 'cae-${environmentName}-${suffix}'
    frontendContainerAppName: 'aca-web-${environmentName}-${suffix}'
    mcpContainerAppName: 'aca-mcp-mem-${environmentName}-${suffix}'
    mcpSearchContainerAppName: 'aca-mcp-srch-${environmentName}-${suffix}'
    backendContainerAppName: 'aca-backend-${environmentName}-${suffix}'
    registryName: 'acr${replace(environmentName, '-', '')}${suffix}'
    entraClientId: appRegistration.appId
    entraTenantId: tenant().tenantId
    openaiEndpoint: aiFoundry.outputs.openaiEndpoint
    gptDeploymentName: aiFoundry.outputs.gptDeploymentName
    gptMiniDeploymentName: aiFoundry.outputs.gptMiniDeploymentName
    embeddingDeploymentName: aiFoundry.outputs.embeddingDeploymentName
    cosmosEndpoint: cosmosDb.outputs.endpoint
    cosmosDatabaseName: cosmosDb.outputs.databaseName
    cosmosContainerName: cosmosDb.outputs.containerName
    aiSearchEndpoint: aiSearch.outputs.endpoint
    aiProjectEndpoint: aiFoundry.outputs.projectEndpoint
    embeddingDimensions: embeddingDimensions
    tags: {
      'azd-env-name': environmentName
      'azd-service-name': 'web'
    }
    mcpTags: {
      'azd-env-name': environmentName
      'azd-service-name': 'mcp-memory'
    }
    mcpSearchTags: {
      'azd-env-name': environmentName
      'azd-service-name': 'mcp-search'
    }
    backendTags: {
      'azd-env-name': environmentName
      'azd-service-name': 'backend'
    }
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: MCP Memory -> Cosmos DB (Built-in Data Contributor)
// 00000000-0000-0000-0000-000000000002 is the Cosmos DB Built-in Data Contributor role
// ---------------------------------------------------------------------------

module mcpCosmosRoleAssignment 'modules/cosmos-role-assignment.bicep' = {
  name: 'mcp-memory-cosmos-role-assignment'
  scope: rg
  params: {
    cosmosAccountName: cosmosDb.outputs.accountName
    principalId: containerApps.outputs.mcpPrincipalId
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: MCP Memory -> Microsoft Foundry (Cognitive Services OpenAI User)
// ---------------------------------------------------------------------------

module mcpOpenAiRoleAssignment 'modules/role-assignment.bicep' = {
  name: 'mcp-memory-openai-role-assignment'
  scope: rg
  params: {
    principalId: containerApps.outputs.mcpPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: Backend -> Microsoft Foundry (Cognitive Services OpenAI User)
// ---------------------------------------------------------------------------

module backendOpenAiRoleAssignment 'modules/role-assignment.bicep' = {
  name: 'backend-openai-role-assignment'
  scope: rg
  params: {
    principalId: containerApps.outputs.backendPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: Backend -> Microsoft Foundry (Azure AI User)
// Allows the backend to use agents and conversations via the AI Projects SDK
// ---------------------------------------------------------------------------

module backendAiUserRoleAssignment 'modules/role-assignment.bicep' = {
  name: 'backend-ai-user-role-assignment'
  scope: rg
  params: {
    principalId: containerApps.outputs.backendPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: AI Search -> Microsoft Foundry (Cognitive Services OpenAI User)
// Allows the ingestion script to call embeddings via the search service identity
// ---------------------------------------------------------------------------

module searchOpenAiRoleAssignment 'modules/role-assignment.bicep' = {
  name: 'search-openai-role-assignment'
  scope: rg
  params: {
    principalId: aiSearch.outputs.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: MCP Search -> AI Search (Search Index Data Reader)
// 1407120a-92aa-4202-b7e9-c0e197c71c8f is the Search Index Data Reader role
// ---------------------------------------------------------------------------

module mcpSearchIndexReaderRoleAssignment 'modules/role-assignment.bicep' = {
  name: 'mcp-search-idx-reader-role-assignment'
  scope: rg
  params: {
    principalId: containerApps.outputs.mcpSearchPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: Current User -> AI Search (Search Service Contributor)
// Allows the signed-in user to create and manage search indexes
// ---------------------------------------------------------------------------

module userSearchServiceRoleAssignment 'modules/role-assignment.bicep' = if (!empty(principalId)) {
  name: 'user-search-svc-role-assignment'
  scope: rg
  params: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: Current User -> AI Search (Search Index Data Contributor)
// Allows the signed-in user to upload documents to search indexes
// ---------------------------------------------------------------------------

module userSearchIndexDataRoleAssignment 'modules/role-assignment.bicep' = if (!empty(principalId)) {
  name: 'user-search-idx-data-role-assignment'
  scope: rg
  params: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: Current User -> Microsoft Foundry (Cognitive Services OpenAI User)
// Allows the signed-in user to call embeddings during the ingestion script
// ---------------------------------------------------------------------------

module userOpenAiRoleAssignment 'modules/role-assignment.bicep' = if (!empty(principalId)) {
  name: 'user-openai-role-assignment'
  scope: rg
  params: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------------
// Outputs — azd maps these to environment variables automatically
// ---------------------------------------------------------------------------

output ENTRA_CLIENT_ID string = appRegistration.appId
output ENTRA_TENANT_ID string = tenant().tenantId
output COSMOS_ENDPOINT string = cosmosDb.outputs.endpoint
output COSMOS_DATABASE string = cosmosDb.outputs.databaseName
output COSMOS_CONTAINER string = cosmosDb.outputs.containerName
output COSMOS_ACCOUNT_NAME string = cosmosDb.outputs.accountName
output AZURE_AI_FOUNDRY_ENDPOINT string = aiFoundry.outputs.endpoint
output FOUNDRY_ENDPOINT string = aiFoundry.outputs.openaiEndpoint
output AZURE_AI_FOUNDRY_NAME string = aiFoundry.outputs.name
output AZURE_AI_FOUNDRY_PROJECT_NAME string = aiFoundry.outputs.projectName
output FOUNDRY_PROJECT_ENDPOINT string = aiFoundry.outputs.projectEndpoint
output AZURE_AI_FOUNDRY_CHAT_DEPLOYMENT string = aiFoundry.outputs.gptDeploymentName
output AZURE_AI_FOUNDRY_EMBEDDING_DEPLOYMENT string = aiFoundry.outputs.embeddingDeploymentName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerApps.outputs.registryLoginServer
output SERVICE_WEB_NAME string = containerApps.outputs.frontendContainerAppName
output SERVICE_WEB_FQDN string = containerApps.outputs.fqdn
output SERVICE_MCP_MEMORY_NAME string = containerApps.outputs.mcpContainerAppName
output SERVICE_MCP_MEMORY_ENDPOINT string = containerApps.outputs.mcpUri
output SERVICE_MCP_SEARCH_NAME string = containerApps.outputs.mcpSearchContainerAppName
output SERVICE_MCP_SEARCH_ENDPOINT string = containerApps.outputs.mcpSearchUri
output SERVICE_BACKEND_NAME string = containerApps.outputs.backendContainerAppName
output SERVICE_BACKEND_ENDPOINT string = containerApps.outputs.backendUri
output SERVICE_WEB_RESOURCE_GROUP string = rg.name
output AZURE_AI_SEARCH_ENDPOINT string = aiSearch.outputs.endpoint
output AZURE_AI_SEARCH_NAME string = aiSearch.outputs.name
output EMBEDDING_DIMENSIONS int = embeddingDimensions
