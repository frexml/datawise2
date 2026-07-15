variable "name_prefix" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "tags" {
  type = map(string)
}

variable "acr_login_server" {
  type = string
}

variable "acr_admin_username" {
  type      = string
  sensitive = true
}

variable "acr_admin_password" {
  type      = string
  sensitive = true
}

variable "identity_id" {
  type = string
}

variable "identity_client_id" {
  type = string
}

variable "key_vault_id" {
  type = string
}

variable "key_vault_uri" {
  type = string
}

variable "image_tag" {
  type        = string
  description = "Image tag for web/worker. Use 'bootstrap' for the first apply (uses a placeholder image) and a real tag thereafter."
}

variable "openai_model" {
  type = string
}

variable "openai_api_key_secret_uri" {
  type = string
}

variable "database_url_secret_uri" {
  type = string
}

variable "redis_url_secret_uri" {
  type = string
}

variable "web_repo" {
  type    = string
  default = "dsx-web"
}

variable "worker_repo" {
  type    = string
  default = "dsx-worker"
}

variable "uploads_storage_account_name" {
  type = string
}

variable "uploads_storage_account_key" {
  type      = string
  sensitive = true
}

variable "uploads_share_name" {
  type = string
}

variable "neo4j_data_share_name" {
  type = string
}

variable "neo4j_password_secret_uri" {
  type = string
}

variable "neo4j_password" {
  type        = string
  sensitive   = true
  description = "Plaintext Neo4j password — used only to build the neo4j container's own inline NEO4J_AUTH secret (composite 'neo4j/<password>' string, which Key Vault stores as the bare password only)."
}

variable "openmetadata_mysql_fqdn" {
  type = string
}

variable "openmetadata_mysql_login" {
  type = string
}

variable "openmetadata_mysql_password" {
  type        = string
  sensitive   = true
  description = "Plaintext OpenMetadata MySQL admin password — used directly as this module's own inline container secret, same pattern as neo4j_password."
}
