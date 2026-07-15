variable "env" {
  type        = string
  description = "Environment name (dev/staging/prod)"
  default     = "dev"
}

variable "location" {
  type        = string
  description = "Azure region"
  default     = "canadacentral"
}

variable "project" {
  type        = string
  description = "Project slug used in resource names"
  default     = "dsxlineage"
}

variable "created_on" {
  type        = string
  description = "ISO date the deployment was applied (inject via -var, never hardcode)"

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.created_on))
    error_message = "created_on must be YYYY-MM-DD (pass via -var=\"created_on=$(date -u +%F)\")."
  }
}

variable "image_tag" {
  type        = string
  description = "Container image tag deployed to web/worker/openmetadata_server. Use 'bootstrap' for the very first apply before any images have been pushed."
}

variable "openai_api_key" {
  type        = string
  description = "OpenAI API key to seed in Key Vault. Source from env (TF_VAR_openai_api_key) — never commit."
  sensitive   = true
}

variable "postgres_admin_password" {
  type        = string
  description = "Initial Postgres admin password. Source from env (TF_VAR_postgres_admin_password) — never commit."
  sensitive   = true
}

variable "openai_model" {
  type        = string
  description = "OpenAI model name used by backend + worker"
  default     = "gpt-4o"
}
