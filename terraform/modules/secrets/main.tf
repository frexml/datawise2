resource "random_string" "suffix" {
  length  = 6
  upper   = false
  lower   = true
  numeric = true
  special = false
}

resource "azurerm_key_vault" "main" {
  # KV names must be 3-24 chars, alphanumeric + hyphens.
  name                       = substr("kv-${var.name_prefix}-${random_string.suffix.result}", 0, 24)
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = false
  purge_protection_enabled   = false # dev only; prod must enable
  soft_delete_retention_days = 7

  tags = var.tags
}

# Deployer (CI / engineer) — full secret management to seed values
resource "azurerm_key_vault_access_policy" "deployer" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = var.tenant_id
  object_id    = var.deployer_object_id

  secret_permissions = ["Get", "List", "Set", "Delete", "Recover", "Purge"]
}

# Container Apps identity — read-only
resource "azurerm_key_vault_access_policy" "apps" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = var.tenant_id
  object_id    = var.app_identity_object_id

  secret_permissions = ["Get", "List"]
}

resource "azurerm_key_vault_secret" "openai_api_key" {
  name         = "openai-api-key"
  value        = var.openai_api_key
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.deployer]
}

resource "azurerm_key_vault_secret" "postgres_admin_password" {
  name         = "postgres-admin-password"
  value        = var.postgres_admin_password
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.deployer]
}

resource "azurerm_key_vault_secret" "database_url" {
  name         = "database-url"
  value        = var.postgres_connection_url
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.deployer]
}

resource "azurerm_key_vault_secret" "redis_url" {
  name         = "redis-url"
  value        = var.redis_connection_url
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.deployer]
}
