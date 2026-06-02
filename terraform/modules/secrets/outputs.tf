output "key_vault_id" {
  value = azurerm_key_vault.main.id
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}

output "openai_api_key_secret_uri" {
  value = azurerm_key_vault_secret.openai_api_key.versionless_id
}

output "database_url_secret_uri" {
  value = azurerm_key_vault_secret.database_url.versionless_id
}

output "redis_url_secret_uri" {
  value = azurerm_key_vault_secret.redis_url.versionless_id
}
