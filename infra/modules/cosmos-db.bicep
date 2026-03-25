@description('Azure region for the Cosmos DB account')
param location string

@description('Cosmos DB account name (must be globally unique)')
param accountName string

@description('Name of the database')
param databaseName string

@description('Name of the container')
param containerName string

@description('Embedding vector dimensions')
param embeddingDimensions int

// ---------------------------------------------------------------------------
// Cosmos DB Account — Serverless, NoSQL API with vector search
// ---------------------------------------------------------------------------

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2025-10-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    capabilities: [
      { name: 'EnableServerless' }
      { name: 'EnableNoSQLVectorSearch' }
    ]
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-10-15' = {
  parent: cosmosAccount
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// ---------------------------------------------------------------------------
// Container — partition key, indexing policy, and vector index from README
// ---------------------------------------------------------------------------

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-10-15' = {
  parent: database
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: ['/user_id']
        kind: 'Hash'
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [
          { path: '/category/?' }
          { path: '/created_at/?' }
          { path: '/updated_at/?' }
          { path: '/tags/[]/?' }
        ]
        excludedPaths: [
          { path: '/content/?' }
          { path: '/embedding/*' }
          { path: '/*' }
        ]
        vectorIndexes: [
          {
            path: '/embedding'
            type: 'quantizedFlat'
          }
        ]
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: '/embedding'
            dataType: 'float32'
            dimensions: embeddingDimensions
            distanceFunction: 'cosine'
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output endpoint string = cosmosAccount.properties.documentEndpoint
output databaseName string = databaseName
output containerName string = containerName
output accountName string = cosmosAccount.name
