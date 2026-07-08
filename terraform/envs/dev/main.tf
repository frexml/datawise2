locals {
  name_prefix = "${var.project}-${var.env}"

  common_tags = {
    env        = var.env
    project    = var.project
    managed_by = "terraform"
    created_on = var.created_on
  }
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name_prefix}"
  location = var.location
  tags     = local.common_tags
}

module "registry" {
  source = "../../modules/registry"

  name_prefix         = local.name_prefix
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.common_tags
}

module "data" {
  source = "../../modules/data"

  name_prefix             = local.name_prefix
  location                = var.location
  resource_group_name     = azurerm_resource_group.main.name
  postgres_admin_password = var.postgres_admin_password
  tags                    = local.common_tags
}

module "secrets" {
  source = "../../modules/secrets"

  name_prefix             = local.name_prefix
  location                = var.location
  resource_group_name     = azurerm_resource_group.main.name
  tenant_id               = data.azurerm_client_config.current.tenant_id
  deployer_object_id      = data.azurerm_client_config.current.object_id
  app_identity_object_id  = module.registry.identity_principal_id
  openai_api_key          = var.openai_api_key
  postgres_admin_password = var.postgres_admin_password
  postgres_connection_url = module.data.postgres_connection_url
  redis_connection_url    = module.data.redis_connection_url
  neo4j_password          = module.data.neo4j_password
  tags                    = local.common_tags
}

module "apps" {
  source = "../../modules/apps"

  name_prefix         = local.name_prefix
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.common_tags

  acr_login_server   = module.registry.acr_login_server
  acr_admin_username = module.registry.acr_admin_username
  acr_admin_password = module.registry.acr_admin_password
  identity_id        = module.registry.identity_id
  identity_client_id = module.registry.identity_client_id
  key_vault_id       = module.secrets.key_vault_id
  key_vault_uri      = module.secrets.key_vault_uri

  uploads_storage_account_name = module.data.uploads_storage_account_name
  uploads_storage_account_key  = module.data.uploads_storage_account_key
  uploads_share_name           = module.data.uploads_share_name
  neo4j_data_share_name        = module.data.neo4j_data_share_name

  image_tag    = var.image_tag
  openai_model = var.openai_model

  openai_api_key_secret_uri = module.secrets.openai_api_key_secret_uri
  database_url_secret_uri   = module.secrets.database_url_secret_uri
  redis_url_secret_uri      = module.secrets.redis_url_secret_uri
  neo4j_password_secret_uri = module.secrets.neo4j_password_secret_uri
  neo4j_password            = module.data.neo4j_password
}
