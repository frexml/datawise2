terraform {
  backend "azurerm" {
    resource_group_name  = "rg-dsxlineage-tfstate-dev"
    storage_account_name = "stdsxlineagetfstatedev"
    container_name       = "tfstate"
    key                  = "dev.terraform.tfstate"
  }
}
