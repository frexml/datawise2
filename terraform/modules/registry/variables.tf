variable "name_prefix" {
  type        = string
  description = "Used to compose globally-unique resource names"
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
