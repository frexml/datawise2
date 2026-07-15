resource "random_string" "suffix" {
  length  = 6
  upper   = false
  lower   = true
  numeric = true
  special = false
}

# Self-hosted Neo4j Community (no managed Azure PaaS offering exists for it,
# unlike Postgres/Redis above) — Terraform owns and generates its password.
resource "random_password" "neo4j" {
  length  = 24
  special = false
}

# OpenMetadata's MySQL — unlike Neo4j, MySQL/InnoDB DOES have a managed Azure
# equivalent, so it isn't self-hosted. (An earlier version of this module
# self-hosted it on an Azure Files volume; InnoDB's redo-log file locking
# doesn't work over SMB and it crash-looped with "Unable to lock
# ./#innodb_redo/#ib_redo0" / assertion failures — the same class of problem
# MySQL's own docs warn about for NFS/network datadirs.)
resource "random_password" "openmetadata_mysql" {
  length  = 24
  special = false
}

resource "azurerm_mysql_flexible_server" "openmetadata" {
  name                   = "mysql-${var.name_prefix}-om-${random_string.suffix.result}"
  resource_group_name    = var.resource_group_name
  location               = var.location
  administrator_login    = "openmetadata_user"
  administrator_password = random_password.openmetadata_mysql.result
  backup_retention_days  = 7
  sku_name               = "B_Standard_B1ms"
  version                = "8.0.21"
  zone                   = "1"
  # public_network_access_enabled is computed for this resource (unlike
  # azurerm_postgresql_flexible_server, where it's settable) — it resolves to
  # enabled automatically since no private-access delegated subnet is configured.

  storage {
    size_gb = 20
  }

  tags = var.tags
}

# Allow other Azure services (Container Apps egress) to reach MySQL in dev.
# Replace with a private endpoint + VNet integration for prod.
resource "azurerm_mysql_flexible_server_firewall_rule" "allow_azure" {
  name                = "allow-azure-services"
  resource_group_name = var.resource_group_name
  server_name         = azurerm_mysql_flexible_server.openmetadata.name
  start_ip_address    = "0.0.0.0"
  end_ip_address      = "0.0.0.0"
}

resource "azurerm_mysql_flexible_database" "openmetadata" {
  name                = "openmetadata_db"
  resource_group_name = var.resource_group_name
  server_name         = azurerm_mysql_flexible_server.openmetadata.name
  charset             = "utf8mb4"
  collation           = "utf8mb4_unicode_ci"
}

# Azure enables this by default (MySQL 8.0.30+ feature: auto-adds an
# invisible PK to any InnoDB table created without one, for replication
# safety). OpenMetadata's own schema-migration scripts assume a table
# without a declared PK genuinely has none — its historical 1.2.0 migration
# does `ALTER TABLE tag ... ADD PRIMARY KEY(id)`, which collides with the
# invisible auto-generated one and fails with "Multiple primary key defined".
# Same migration succeeds unmodified against local Docker MySQL, which
# doesn't set this. Must be off before openmetadata_migrate ever runs.
resource "azurerm_mysql_flexible_server_configuration" "disable_invisible_pk" {
  name                = "sql_generate_invisible_primary_key"
  resource_group_name = var.resource_group_name
  server_name         = azurerm_mysql_flexible_server.openmetadata.name
  value               = "OFF"
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

# Durable storage for Neo4j's /data directory across container restarts.
resource "azurerm_storage_share" "neo4j_data" {
  name                 = "neo4j-data"
  storage_account_name = azurerm_storage_account.uploads.name
  quota                = 10 # GB — demo scale
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
