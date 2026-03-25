@description('Name of the Cosmos DB account')
param cosmosAccountName string

@description('Principal ID to grant access to')
param principalId string

// Reference the existing Cosmos DB account
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2025-10-15' existing = {
  name: cosmosAccountName
}

// Cosmos DB Built-in Data Contributor role
// This is a Cosmos DB-level RBAC role (not ARM), identified by its well-known GUID.
var dataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource sqlRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-10-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, principalId, dataContributorRoleId)
  properties: {
    principalId: principalId
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${dataContributorRoleId}'
    scope: cosmosAccount.id
  }
}
