output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "acr_login_server" {
  description = "Push images to this registry: docker push <acr_login_server>/<repo>:<tag>"
  value       = module.registry.acr_login_server
}

output "acr_name" {
  description = "Use with: az acr login --name <acr_name>"
  value       = module.registry.acr_name
}

output "backend_fqdn" {
  value = module.apps.backend_fqdn
}

output "frontend_fqdn" {
  value = module.apps.frontend_fqdn
}

output "key_vault_name" {
  value = module.secrets.key_vault_name
}
