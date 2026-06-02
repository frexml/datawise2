output "backend_fqdn" {
  value = length(azurerm_container_app.backend) > 0 ? azurerm_container_app.backend[0].ingress[0].fqdn : null
}

output "frontend_fqdn" {
  value = length(azurerm_container_app.frontend) > 0 ? azurerm_container_app.frontend[0].ingress[0].fqdn : null
}

output "container_app_environment_id" {
  value = azurerm_container_app_environment.main.id
}
