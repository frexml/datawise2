output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "postgres_database" {
  value = azurerm_postgresql_flexible_server_database.main.name
}

output "postgres_admin_login" {
  value = azurerm_postgresql_flexible_server.main.administrator_login
}

output "postgres_connection_url" {
  description = "SQLAlchemy-compatible connection URL"
  value = format(
    "postgresql://%s:%s@%s:5432/%s?sslmode=require",
    azurerm_postgresql_flexible_server.main.administrator_login,
    var.postgres_admin_password,
    azurerm_postgresql_flexible_server.main.fqdn,
    azurerm_postgresql_flexible_server_database.main.name,
  )
  sensitive = true
}

output "uploads_storage_account_name" {
  value = azurerm_storage_account.uploads.name
}

output "uploads_storage_account_key" {
  value     = azurerm_storage_account.uploads.primary_access_key
  sensitive = true
}

output "uploads_share_name" {
  value = azurerm_storage_share.uploads.name
}

output "redis_hostname" {
  value = azurerm_redis_cache.main.hostname
}

output "redis_connection_url" {
  description = "Celery-compatible Redis URL with TLS. ssl_cert_reqs is required by Celery's redis backend for rediss:// URLs."
  value = format(
    "rediss://:%s@%s:%d/0?ssl_cert_reqs=CERT_REQUIRED",
    azurerm_redis_cache.main.primary_access_key,
    azurerm_redis_cache.main.hostname,
    azurerm_redis_cache.main.ssl_port,
  )
  sensitive = true
}

output "neo4j_password" {
  value     = random_password.neo4j.result
  sensitive = true
}

output "neo4j_data_share_name" {
  value = azurerm_storage_share.neo4j_data.name
}

# Estate-only: openmetadata outputs removed — see archive/etl-v1 and v1-etl-final.
