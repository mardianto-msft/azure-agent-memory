#!/bin/sh
cat > /usr/share/nginx/html/config.json << EOF
{
  "clientId": "${ENTRA_CLIENT_ID:-}",
  "tenantId": "${ENTRA_TENANT_ID:-}",
  "backendUrl": "${BACKEND_URL:-}"
}
EOF
exec nginx -g 'daemon off;'
