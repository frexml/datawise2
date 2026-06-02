resource "random_string" "acr_suffix" {
  length  = 6
  upper   = false
  lower   = true
  numeric = true
  special = false
}

resource "azurerm_container_registry" "main" {
  # ACR names must be 5-50 chars, alphanumeric only. Strip hyphens from prefix.
  name                = "acr${replace(var.name_prefix, "-", "")}${random_string.acr_suffix.result}"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Basic"
  # admin_enabled = true because the tenant restricts `Microsoft.Authorization/roleAssignments/write`,
  # so the cleaner AcrPull-via-managed-identity path isn't available. Container Apps authenticate
  # with the admin username/password (stored as a Container App secret).
  admin_enabled = true
  tags          = var.tags
}

# Still useful: Container Apps use this identity for Key Vault secret retrieval
# (access policies, not RBAC roles, so no tenant restriction).
resource "azurerm_user_assigned_identity" "apps" {
  name                = "uami-${var.name_prefix}-apps"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}
