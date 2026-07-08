locals {
  # First apply uses image_tag="bootstrap": creates everything EXCEPT the three
  # Container Apps. Build/push images, then re-apply with a real tag to create
  # the apps. Avoids the placeholder-image health-probe failures.
  apps_enabled = var.image_tag != "bootstrap" ? 1 : 0

  backend_image  = "${var.acr_login_server}/${var.backend_repo}:${var.image_tag}"
  frontend_image = "${var.acr_login_server}/${var.frontend_repo}:${var.image_tag}"
  worker_image   = "${var.acr_login_server}/${var.worker_repo}:${var.image_tag}"
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${var.name_prefix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${var.name_prefix}"
  location                   = var.location
  resource_group_name        = var.resource_group_name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = var.tags
}

# Registers the Azure Files share with the CAE so Container Apps can mount it.
resource "azurerm_container_app_environment_storage" "uploads" {
  name                         = "uploads"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = var.uploads_storage_account_name
  share_name                   = var.uploads_share_name
  access_key                   = var.uploads_storage_account_key
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app_environment_storage" "neo4j_data" {
  name                         = "neo4j-data"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = var.uploads_storage_account_name
  share_name                   = var.neo4j_data_share_name
  access_key                   = var.uploads_storage_account_key
  access_mode                  = "ReadWrite"
}

# ─── Neo4j Community (self-hosted, internal-only) ─────────────────────
# Public image — no ACR registry needed. Internal TCP ingress means only
# other Container Apps in this environment (backend/worker) can reach it.
resource "azurerm_container_app" "neo4j" {
  name                         = "ca-${var.name_prefix}-neo4j"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  secret {
    name  = "neo4j-auth"
    value = "neo4j/${var.neo4j_password}"
  }

  template {
    min_replicas = 1
    max_replicas = 1 # single instance — Neo4j Community has no clustering

    container {
      name   = "neo4j"
      image  = "neo4j:5-community"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name        = "NEO4J_AUTH"
        secret_name = "neo4j-auth"
      }

      volume_mounts {
        name = "neo4j-data"
        path = "/data"
      }
    }

    volume {
      name         = "neo4j-data"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.neo4j_data.name
    }
  }

  ingress {
    external_enabled = false
    target_port      = 7687
    exposed_port     = 7687
    transport        = "tcp"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# ─── Backend (FastAPI) ────────────────────────────────────────────────
resource "azurerm_container_app" "backend" {
  count = local.apps_enabled

  name                         = "ca-${var.name_prefix}-backend"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "acr-admin-password"
  }

  secret {
    name  = "acr-admin-password"
    value = var.acr_admin_password
  }

  secret {
    name                = "openai-api-key"
    key_vault_secret_id = var.openai_api_key_secret_uri
    identity            = var.identity_id
  }
  secret {
    name                = "database-url"
    key_vault_secret_id = var.database_url_secret_uri
    identity            = var.identity_id
  }
  secret {
    name                = "redis-url"
    key_vault_secret_id = var.redis_url_secret_uri
    identity            = var.identity_id
  }
  secret {
    name                = "neo4j-password"
    key_vault_secret_id = var.neo4j_password_secret_uri
    identity            = var.identity_id
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "backend"
      image  = local.backend_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-api-key"
      }
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "CELERY_BROKER_URL"
        secret_name = "redis-url"
      }
      env {
        name        = "CELERY_RESULT_BACKEND"
        secret_name = "redis-url"
      }
      env {
        name  = "OPENAI_MODEL"
        value = var.openai_model
      }
      env {
        name  = "NEO4J_URI"
        value = "bolt://${azurerm_container_app.neo4j.ingress[0].fqdn}:7687"
      }
      env {
        name  = "NEO4J_USER"
        value = "neo4j"
      }
      env {
        name        = "NEO4J_PASSWORD"
        secret_name = "neo4j-password"
      }

      volume_mounts {
        name = "uploads"
        path = "/app/uploads"
      }
    }

    volume {
      name         = "uploads"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.uploads.name
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# ─── Frontend (nginx-served Vite build) ───────────────────────────────
resource "azurerm_container_app" "frontend" {
  count = local.apps_enabled

  name                         = "ca-${var.name_prefix}-frontend"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "acr-admin-password"
  }

  secret {
    name  = "acr-admin-password"
    value = var.acr_admin_password
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "frontend"
      image  = local.frontend_image
      cpu    = 0.25
      memory = "0.5Gi"

      # nginx.conf.template proxies /api/* to ${BACKEND_URL}
      env {
        name  = "BACKEND_URL"
        value = "https://${azurerm_container_app.backend[0].ingress[0].fqdn}"
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 80
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# ─── Celery worker (no ingress) ───────────────────────────────────────
resource "azurerm_container_app" "worker" {
  count = local.apps_enabled

  name                         = "ca-${var.name_prefix}-worker"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "acr-admin-password"
  }

  secret {
    name  = "acr-admin-password"
    value = var.acr_admin_password
  }

  secret {
    name                = "openai-api-key"
    key_vault_secret_id = var.openai_api_key_secret_uri
    identity            = var.identity_id
  }
  secret {
    name                = "database-url"
    key_vault_secret_id = var.database_url_secret_uri
    identity            = var.identity_id
  }
  secret {
    name                = "redis-url"
    key_vault_secret_id = var.redis_url_secret_uri
    identity            = var.identity_id
  }
  secret {
    name                = "neo4j-password"
    key_vault_secret_id = var.neo4j_password_secret_uri
    identity            = var.identity_id
  }

  template {
    min_replicas = 1
    max_replicas = 5

    container {
      name    = "worker"
      image   = local.worker_image
      cpu     = 1.0
      memory  = "2Gi"
      command = ["celery", "-A", "dsxlineage.worker.celery_app", "worker", "--loglevel=info"]

      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-api-key"
      }
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "CELERY_BROKER_URL"
        secret_name = "redis-url"
      }
      env {
        name        = "CELERY_RESULT_BACKEND"
        secret_name = "redis-url"
      }
      env {
        name  = "OPENAI_MODEL"
        value = var.openai_model
      }
      env {
        name  = "NEO4J_URI"
        value = "bolt://${azurerm_container_app.neo4j.ingress[0].fqdn}:7687"
      }
      env {
        name  = "NEO4J_USER"
        value = "neo4j"
      }
      env {
        name        = "NEO4J_PASSWORD"
        secret_name = "neo4j-password"
      }

      volume_mounts {
        name = "uploads"
        path = "/app/uploads"
      }
    }

    volume {
      name         = "uploads"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.uploads.name
    }
  }
}
