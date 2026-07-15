output "web_fqdn" {
  value = length(azurerm_container_app.web) > 0 ? azurerm_container_app.web[0].ingress[0].fqdn : null
}

output "openmetadata_server_fqdn" {
  value = length(azurerm_container_app.openmetadata_server) > 0 ? azurerm_container_app.openmetadata_server[0].ingress[0].fqdn : null
}

output "openmetadata_migrate_job_name" {
  value = azurerm_container_app_job.openmetadata_migrate.name
}

output "container_app_environment_id" {
  value = azurerm_container_app_environment.main.id
}
