export interface AuthConfig {
  clientId: string;
  tenantId: string;
}

export async function loadAuthConfig(): Promise<AuthConfig> {
  const response = await fetch('/config.json');
  if (!response.ok) {
    throw new Error('Failed to load auth configuration');
  }
  return response.json();
}
