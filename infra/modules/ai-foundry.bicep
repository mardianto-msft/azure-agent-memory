@description('Name of the Microsoft Foundry resource')
param name string

@description('Location for the Microsoft Foundry resource')
param location string

@description('Tags for the Microsoft Foundry resource')
param tags object = {}

@description('GPT model name')
param gptModelName string = 'gpt-5.1'

@description('GPT model version')
param gptModelVersion string = '2025-11-13'

@description('GPT model capacity (in thousands of TPM)')
param gptCapacity int = 100

@description('GPT mini model name (for memory extraction)')
param gptMiniModelName string = 'gpt-5-mini'

@description('GPT mini model version')
param gptMiniModelVersion string = '2025-08-07'

@description('GPT mini model capacity (in thousands of TPM)')
param gptMiniCapacity int = 100

@description('Embedding model name')
param embeddingModelName string = 'text-embedding-3-large'

@description('Embedding model version')
param embeddingModelVersion string = '1'

@description('Embedding model capacity (in thousands of TPM)')
param embeddingCapacity int = 50

@description('Deployment type for models')
param deploymentType string = 'GlobalStandard'

@description('Project display name')
param projectDisplayName string = 'Agent Memory Project'

// ---------------------------------------------------------------------------
// AI Services account (Foundry resource) with project management enabled
// ---------------------------------------------------------------------------

resource aiService 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
  }
}

// ---------------------------------------------------------------------------
// GPT Model Deployment
// ---------------------------------------------------------------------------

resource gptDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiService
  name: gptModelName
  properties: {
    model: {
      format: 'OpenAI'
      name: gptModelName
      version: gptModelVersion
    }
  }
  sku: {
    name: deploymentType
    capacity: gptCapacity
  }
}

// ---------------------------------------------------------------------------
// Embedding Model Deployment
// ---------------------------------------------------------------------------

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiService
  name: embeddingModelName
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
  }
  sku: {
    name: deploymentType
    capacity: embeddingCapacity
  }
  dependsOn: [gptDeployment]
}

resource gptMiniDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiService
  name: gptMiniModelName
  properties: {
    model: {
      format: 'OpenAI'
      name: gptMiniModelName
      version: gptMiniModelVersion
    }
  }
  sku: {
    name: deploymentType
    capacity: gptMiniCapacity
  }
  dependsOn: [embeddingDeployment]
}

// ---------------------------------------------------------------------------
// Foundry Project
// ---------------------------------------------------------------------------

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-10-01-preview' = {
  parent: aiService
  name: '${name}-project'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectDisplayName
    description: 'Microsoft Foundry Project for Agent Memory application'
  }
  dependsOn: [
    gptDeployment
    embeddingDeployment
    gptMiniDeployment
  ]
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output id string = aiService.id
output name string = aiService.name
output endpoint string = aiService.properties.endpoint
output openaiEndpoint string = 'https://${name}.openai.azure.com/'
output principalId string = aiService.identity.principalId
output projectName string = foundryProject.name
output projectEndpoint string = 'https://${name}.services.ai.azure.com/api/projects/${foundryProject.name}'
output projectPrincipalId string = foundryProject.identity.principalId
output gptDeploymentName string = gptModelName
output gptMiniDeploymentName string = gptMiniModelName
output embeddingDeploymentName string = embeddingModelName
