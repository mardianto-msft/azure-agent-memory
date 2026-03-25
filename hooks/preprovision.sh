#!/bin/bash
set -e

# Get the current user's principal ID and set it as an azd environment variable.
# This is used by Bicep to create role assignments for the signed-in user.

PRINCIPAL_ID=$(az ad signed-in-user show --query id --output tsv)

if [ -z "$PRINCIPAL_ID" ]; then
  echo "Warning: Could not retrieve current user's principal ID. Role assignments will be skipped."
else
  echo "Setting AZURE_PRINCIPAL_ID to: $PRINCIPAL_ID"
  azd env set AZURE_PRINCIPAL_ID "$PRINCIPAL_ID"
fi
