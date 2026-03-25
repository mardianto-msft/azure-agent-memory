#!/bin/bash
set -e

echo "=== Post-provision: configuring secrets, demo users, and generating .env ==="

# Helper: safely read an azd env value (returns empty string if key not found)
azd_get() {
  local val
  if val=$(azd env get-value "$1" 2>/dev/null) && [ $? -eq 0 ]; then
    echo "$val"
  else
    echo ""
  fi
}

# --- Entra ID client secret (skip if already stored) ---
APP_ID=$(azd env get-value ENTRA_CLIENT_ID)
SECRET=$(azd_get ENTRA_CLIENT_SECRET)
if [ -z "$SECRET" ]; then
  echo "Creating client secret for app ${APP_ID}..."
  SECRET=$(az ad app credential reset \
    --id "$APP_ID" \
    --display-name "demo-secret" \
    --years 1 \
    --query password \
    --output tsv)
  azd env set ENTRA_CLIENT_SECRET "$SECRET"
else
  echo "Client secret already exists in azd env, skipping."
fi

# --- Demo users (skip if already created) ---
TENANT_DOMAIN=$(az rest --method GET --url "https://graph.microsoft.com/v1.0/domains" \
  --query "value[?isDefault].id" --output tsv)

EXISTING_PASSWORD=$(azd_get DEMO_USER_PASSWORD)
if [ -n "$EXISTING_PASSWORD" ]; then
  DEMO_PASSWORD="$EXISTING_PASSWORD"
  echo "Demo user password already exists in azd env, reusing."
else
  DEMO_PASSWORD="AgentMem!Demo2027"
fi

create_demo_user() {
  local display_name=$1
  local mail_nickname=$2
  local upn="${mail_nickname}@${TENANT_DOMAIN}"

  # Check if user already exists
  EXISTING=$(az ad user list --filter "userPrincipalName eq '${upn}'" --query "[0].id" --output tsv 2>/dev/null || true)
  if [ -n "$EXISTING" ]; then
    echo "User ${upn} already exists, skipping."
  else
    echo "Creating user ${upn}..."
    az ad user create \
      --display-name "$display_name" \
      --mail-nickname "$mail_nickname" \
      --user-principal-name "$upn" \
      --password "$DEMO_PASSWORD" \
      --force-change-password-next-sign-in false
  fi
}

create_demo_user "Demo Alice" "demo-alice"
create_demo_user "Demo Bob" "demo-bob"

azd env set DEMO_ALICE_UPN "demo-alice@${TENANT_DOMAIN}"
azd env set DEMO_BOB_UPN "demo-bob@${TENANT_DOMAIN}"
azd env set DEMO_USER_PASSWORD "$DEMO_PASSWORD"

# --- Update app registration redirect URIs with Container App FQDN ---
FQDN=$(azd_get SERVICE_WEB_FQDN)
if [ -n "$FQDN" ]; then
  echo "Adding https://${FQDN} as SPA redirect URI..."
  OBJECT_ID=$(az ad app show --id "$APP_ID" --query id --output tsv)
  az rest --method PATCH \
    --uri "https://graph.microsoft.com/v1.0/applications/${OBJECT_ID}" \
    --headers "Content-Type=application/json" \
    --body "{\"spa\":{\"redirectUris\":[\"http://localhost:5173\",\"https://${FQDN}\"]}}"
else
  echo "Container App FQDN not available, skipping redirect URI update."
fi

# --- Generate .env file ---
echo "Writing .env file..."
cat > .env << EOF
# Entra ID
ENTRA_CLIENT_ID=$(azd env get-value ENTRA_CLIENT_ID)
ENTRA_CLIENT_SECRET=${SECRET}
ENTRA_TENANT_ID=$(azd env get-value ENTRA_TENANT_ID)

# Cosmos DB
COSMOS_ENDPOINT=$(azd env get-value COSMOS_ENDPOINT)
COSMOS_DATABASE=$(azd env get-value COSMOS_DATABASE)
COSMOS_CONTAINER=$(azd env get-value COSMOS_CONTAINER)

# Microsoft Foundry
AZURE_AI_FOUNDRY_ENDPOINT=$(azd env get-value AZURE_AI_FOUNDRY_ENDPOINT)
FOUNDRY_ENDPOINT=$(azd env get-value FOUNDRY_ENDPOINT)
AZURE_AI_FOUNDRY_CHAT_DEPLOYMENT=$(azd env get-value AZURE_AI_FOUNDRY_CHAT_DEPLOYMENT)
AZURE_AI_FOUNDRY_EMBEDDING_DEPLOYMENT=$(azd env get-value AZURE_AI_FOUNDRY_EMBEDDING_DEPLOYMENT)

# Azure AI Search
AZURE_AI_SEARCH_ENDPOINT=$(azd env get-value AZURE_AI_SEARCH_ENDPOINT)
AZURE_AI_SEARCH_NAME=$(azd env get-value AZURE_AI_SEARCH_NAME)
EOF

# --- Run knowledge base ingestion ---

# Export environment variables from azd for the ingestion script
echo "Setting up environment variables for ingestion..."
export FOUNDRY_ENDPOINT=$(azd env get-value FOUNDRY_ENDPOINT)
export AZURE_AI_FOUNDRY_EMBEDDING_DEPLOYMENT=$(azd env get-value AZURE_AI_FOUNDRY_EMBEDDING_DEPLOYMENT)
export AZURE_AI_SEARCH_ENDPOINT=$(azd env get-value AZURE_AI_SEARCH_ENDPOINT)
export EMBEDDING_DIMENSIONS=$(azd env get-value EMBEDDING_DIMENSIONS)

if [ -z "$FOUNDRY_ENDPOINT" ] || [ -z "$AZURE_AI_SEARCH_ENDPOINT" ]; then
  echo "Error: Required environment variables not found in azd env"
  exit 1
fi

# Ensure uv is available (works across environments)
if ! command -v uv &> /dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Create and activate virtual environment
if [ ! -d ".memory" ]; then
  echo "Creating virtual environment..."
  uv venv .memory
fi
source .memory/bin/activate

echo "Installing ingestion script dependencies..."
uv pip install -q -r scripts/requirements.txt

echo "Running knowledge base ingestion..."
python3 scripts/ingest_knowledge.py

echo "=== Done! .env file created with all configuration values. ==="

echo ""
echo "=== Web App ==="
FQDN=$(azd_get SERVICE_WEB_FQDN)
if [ -n "$FQDN" ]; then
  echo "URL: https://${FQDN}"
fi
echo ""
echo "=== Demo User Credentials ==="
echo "Alice: demo-alice@${TENANT_DOMAIN}"
echo "Bob:   demo-bob@${TENANT_DOMAIN}"
echo "Password: ${DEMO_PASSWORD}"
echo "=============================="
