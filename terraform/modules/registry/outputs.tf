output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "acr_id" {
  value = azurerm_container_registry.main.id
}

output "acr_admin_username" {
  value     = azurerm_container_registry.main.admin_username
  sensitive = true
}

output "acr_admin_password" {
  value     = azurerm_container_registry.main.admin_password
  sensitive = true
}

output "identity_id" {
  value = azurerm_user_assigned_identity.apps.id
}

output "identity_principal_id" {
  value = azurerm_user_assigned_identity.apps.principal_id
}

output "identity_client_id" {
  value = azurerm_user_assigned_identity.apps.client_id
}
