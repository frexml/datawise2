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

output "web_fqdn" {
  value = module.apps.web_fqdn
}

output "service_url" {
  description = "Public HTTPS URL of the app — printed by scripts/deploy_azure.sh"
  value       = module.apps.web_fqdn != null ? "https://${module.apps.web_fqdn}" : null
}

output "openmetadata_server_fqdn" {
  value = module.apps.openmetadata_server_fqdn
}

output "openmetadata_url" {
  description = "Public HTTPS URL of the OpenMetadata governance catalog"
  value       = module.apps.openmetadata_server_fqdn != null ? "https://${module.apps.openmetadata_server_fqdn}" : null
}

output "openmetadata_migrate_job_name" {
  description = "Container App Job name — trigger with: az containerapp job start -n <this> -g <resource_group_name>"
  value       = module.apps.openmetadata_migrate_job_name
}

output "key_vault_name" {
  value = module.secrets.key_vault_name
}
