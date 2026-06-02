resource "random_string" "suffix" {
  length  = 6
  upper   = false
  lower   = true
  numeric = true
  special = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${var.name_prefix}-${random_string.suffix.result}"
  resource_group_name           = var.resource_group_name
  location                      = var.location
  version                       = "16"
  administrator_login           = var.postgres_admin_login
  administrator_password        = var.postgres_admin_password
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  backup_retention_days         = 7
  public_network_access_enabled = true # dev only; prod must use private endpoint
  zone                          = "1"

  tags = var.tags
}

# Allow other Azure services (Container Apps egress) to reach Postgres in dev.
# Replace with a private endpoint + VNet integration for prod.
resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = var.postgres_database
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_storage_account" "uploads" {
  # 3-24 chars, lowercase alphanumeric, globally unique
  name                          = substr("st${replace(var.name_prefix, "-", "")}up${random_string.suffix.result}", 0, 24)
  resource_group_name           = var.resource_group_name
  location                      = var.location
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  min_tls_version               = "TLS1_2"
  public_network_access_enabled = true # dev only

  tags = var.tags
}

resource "azurerm_storage_share" "uploads" {
  name                 = "uploads"
  storage_account_name = azurerm_storage_account.uploads.name
  quota                = 50 # GB
}

resource "azurerm_redis_cache" "main" {
  name                          = "redis-${var.name_prefix}-${random_string.suffix.result}"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  capacity                      = 0
  family                        = "C"
  sku_name                      = "Basic"
  non_ssl_port_enabled          = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = true # dev only

  tags = var.tags
}
