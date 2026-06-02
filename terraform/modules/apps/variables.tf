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
  description = "Image tag for backend/frontend/worker. Use 'bootstrap' for the first apply (uses a placeholder image) and a real tag thereafter."
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

variable "backend_repo" {
  type    = string
  default = "dsx-backend"
}

variable "frontend_repo" {
  type    = string
  default = "dsx-frontend"
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
