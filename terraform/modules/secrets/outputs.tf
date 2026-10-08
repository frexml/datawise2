output "key_vault_id" {
  value = azurerm_key_vault.main.id
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}

output "openrouter_api_key_secret_uri" {
  value = azurerm_key_vault_secret.openrouter_api_key.versionless_id
}

output "database_url_secret_uri" {
  value = azurerm_key_vault_secret.database_url.versionless_id
}

output "redis_url_secret_uri" {
  value = azurerm_key_vault_secret.redis_url.versionless_id
}

output "neo4j_password_secret_uri" {
  value = azurerm_key_vault_secret.neo4j_password.versionless_id
}

# Estate-only: openmetadata output removed.
