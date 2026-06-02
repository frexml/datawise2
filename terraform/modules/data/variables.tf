variable "name_prefix" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "postgres_admin_password" {
  type      = string
  sensitive = true
}

variable "tags" {
  type = map(string)
}

variable "postgres_admin_login" {
  type    = string
  default = "dsxadmin"
}

variable "postgres_database" {
  type    = string
  default = "dsx_db"
}
