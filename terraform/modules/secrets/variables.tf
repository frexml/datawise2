variable "name_prefix" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "deployer_object_id" {
  type        = string
  description = "Object ID of the principal running terraform — gets temporary set/get rights to seed secrets"
}

variable "app_identity_object_id" {
  type        = string
  description = "Object ID of the user-assigned identity used by Container Apps — gets get/list rights only"
}

variable "openai_api_key" {
  type      = string
  sensitive = true
}

variable "postgres_admin_password" {
  type      = string
  sensitive = true
}

variable "postgres_connection_url" {
  type      = string
  sensitive = true
}

variable "redis_connection_url" {
  type      = string
  sensitive = true
}

variable "neo4j_password" {
  type      = string
  sensitive = true
}

variable "openmetadata_mysql_password" {
  type      = string
  sensitive = true
}

variable "tags" {
  type = map(string)
}
