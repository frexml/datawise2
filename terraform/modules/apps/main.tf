locals {
  # First apply uses image_tag="bootstrap": creates everything EXCEPT the
  # image-backed Container Apps (web, worker, openmetadata_server). Build/push
  # images, run the openmetadata_migrate job, then re-apply with a real tag.
  # Avoids placeholder-image health-probe failures and lets the migrate job
  # run against a healthy, schema-free MySQL before the server ever starts.
  apps_enabled = var.image_tag != "bootstrap" ? 1 : 0

  web_image    = "${var.acr_login_server}/${var.web_repo}:${var.image_tag}"
  worker_image = "${var.acr_login_server}/${var.worker_repo}:${var.image_tag}"

  openmetadata_server_fqdn = local.apps_enabled == 1 ? azurerm_container_app.openmetadata_server[0].ingress[0].fqdn : ""
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

# ─── Web (FastAPI + Vite build behind Caddy, one image, one URL) ──────
# Replaces the old separate backend/frontend Container Apps. Caddy listens on
# :8080, serves the built frontend statically, and reverse-proxies /api/* to
# the backend on 127.0.0.1:8000 inside the same container — no cross-FQDN
# proxying, no CORS, no Host-header/SNI gymnastics (see terraform/README.md's
# old troubleshooting entry for what that used to require).
resource "azurerm_container_app" "web" {
  count = local.apps_enabled

  name                         = "ca-${var.name_prefix}-web"
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
      name   = "web"
      image  = local.web_image
      cpu    = 1.0
      memory = "2Gi" # backend + frontend static serving + Caddy in one container

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
      env {
        name  = "OPENMETADATA_API_URL"
        value = "https://${local.openmetadata_server_fqdn}/api/v1"
      }
      env {
        name  = "OPENMETADATA_UI_URL"
        value = "https://${local.openmetadata_server_fqdn}"
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
    target_port      = 8080
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
      env {
        name  = "OPENMETADATA_API_URL"
        value = "https://${local.openmetadata_server_fqdn}/api/v1"
      }
      env {
        name  = "OPENMETADATA_UI_URL"
        value = "https://${local.openmetadata_server_fqdn}"
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

# OpenMetadata's MySQL is a managed azurerm_mysql_flexible_server (module
# "data") — NOT self-hosted here. InnoDB's redo-log file locking doesn't work
# over Azure Files (SMB); it crash-looped with "Unable to lock
# ./#innodb_redo/#ib_redo0" the one time this was tried as a Container App.
# Elasticsearch below stays self-hosted since it has no such managed Azure
# equivalent and doesn't share MySQL's file-locking requirements.

# ─── OpenMetadata Elasticsearch (self-hosted, internal-only) ──────────
# Ephemeral storage — same tradeoff already documented for local dev: index
# bootstrapping takes a bit after a fresh start, but nothing here is a
# system of record (Postgres/mysql are).
resource "azurerm_container_app" "openmetadata_elasticsearch" {
  name                         = "ca-${var.name_prefix}-om-es"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "elasticsearch"
      image  = "docker.elastic.co/elasticsearch/elasticsearch:7.16.3"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "discovery.type"
        value = "single-node"
      }
      env {
        name  = "ES_JAVA_OPTS"
        value = "-Xms512m -Xmx512m"
      }
    }
  }

  # External, not internal-only: Container Apps Jobs (openmetadata_migrate)
  # don't reliably reach internal-ingress-only sibling apps in the same
  # environment — confirmed via a real failed migrate run where DNS resolved
  # the internal FQDN fine but every TCP connect attempt timed out, while ES
  # itself was independently confirmed healthy (its own logs showed a clean,
  # continuously-green cluster). openmetadata_server (a regular Container
  # App, not a Job) will also reach ES fine over this same external ingress.
  # Tradeoff: ES has no built-in auth (xpack.security.enabled=false, matching
  # local dev) and only holds a derived search index of catalog metadata —
  # not the source of truth (MySQL is) — but this is still a real exposure.
  ingress {
    external_enabled = true
    target_port      = 9200
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# ─── OpenMetadata schema migration (one-shot job) ──────────────────────
# openmetadata_server's own entrypoint does NOT run this — a fresh MySQL
# volume crash-loops forever on "Table 'openmetadata_db.ACT_GE_PROPERTY'
# doesn't exist" without it (same root cause fixed locally in
# docker-compose.yml's openmetadata_migrate service). Azure Container Apps
# has no docker-compose-style health-gated startup ordering, so this runs as
# a manually-triggered Job: the deploy script starts it and waits for
# completion between the bootstrap apply (creates mysql/ES) and the release
# apply (creates openmetadata_server). Idempotent — safe to (re)run on an
# already-migrated database too.
resource "azurerm_container_app_job" "openmetadata_migrate" {
  name                         = "caj-${var.name_prefix}-om-migrate"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  location                     = var.location
  tags                         = var.tags

  replica_timeout_in_seconds = 600
  replica_retry_limit        = 1

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  secret {
    name  = "mysql-password"
    value = var.openmetadata_mysql_password
  }

  template {
    container {
      name    = "migrate"
      image   = "openmetadata/server:1.12.6"
      cpu     = 1.0
      memory  = "2Gi"
      command = ["/opt/openmetadata/bootstrap/openmetadata-ops.sh", "migrate"]

      env {
        name  = "DB_DRIVER_CLASS"
        value = "com.mysql.cj.jdbc.Driver"
      }
      env {
        name  = "DB_SCHEME"
        value = "mysql"
      }
      # DB_USE_SSL is not a real openmetadata.yaml key — the JDBC URL's query
      # string comes from DB_PARAMS alone, which defaults to "...&useSSL=false&...".
      # Azure Database for MySQL Flexible Server enforces require_secure_transport=ON,
      # so connecting with the default (SSL off) is rejected outright with
      # "Connections using insecure transport are prohibited" — this override is required.
      env {
        name  = "DB_PARAMS"
        value = "allowPublicKeyRetrieval=true&useSSL=true&requireSSL=true&serverTimezone=UTC"
      }
      env {
        name  = "DB_USER"
        value = var.openmetadata_mysql_login
      }
      env {
        name        = "DB_USER_PASSWORD"
        secret_name = "mysql-password"
      }
      env {
        name  = "DB_HOST"
        value = var.openmetadata_mysql_fqdn
      }
      env {
        name  = "DB_PORT"
        value = "3306"
      }
      env {
        name  = "OM_DATABASE"
        value = "openmetadata_db"
      }
      env {
        name  = "ELASTICSEARCH_HOST"
        value = azurerm_container_app.openmetadata_elasticsearch.ingress[0].fqdn
      }
      env {
        name  = "ELASTICSEARCH_PORT"
        value = "443" # external ingress always terminates TLS on 443, proxying to the container's 9200 internally
      }
      env {
        name  = "ELASTICSEARCH_SCHEME"
        value = "https"
      }
    }
  }
}

# ─── OpenMetadata server (governance catalog UI + API) ─────────────────
# External ingress: the catalog_url DataWise pushes to Job records must be
# browser-clickable, and the backend/worker call the same FQDN server-side.
# Gated on apps_enabled like web/worker — only created on the release apply,
# once openmetadata_migrate has already run successfully against mysql.
resource "azurerm_container_app" "openmetadata_server" {
  count = local.apps_enabled

  name                         = "ca-${var.name_prefix}-om-server"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  secret {
    name  = "mysql-password"
    value = var.openmetadata_mysql_password
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "server"
      image  = "openmetadata/server:1.12.6"
      cpu    = 2.0
      memory = "4Gi" # JVM — undersizing this is the single most common OpenMetadata deploy failure

      env {
        name  = "DB_DRIVER_CLASS"
        value = "com.mysql.cj.jdbc.Driver"
      }
      env {
        name  = "DB_SCHEME"
        value = "mysql"
      }
      # DB_USE_SSL is not a real openmetadata.yaml key — the JDBC URL's query
      # string comes from DB_PARAMS alone, which defaults to "...&useSSL=false&...".
      # Azure Database for MySQL Flexible Server enforces require_secure_transport=ON,
      # so connecting with the default (SSL off) is rejected outright with
      # "Connections using insecure transport are prohibited" — this override is required.
      env {
        name  = "DB_PARAMS"
        value = "allowPublicKeyRetrieval=true&useSSL=true&requireSSL=true&serverTimezone=UTC"
      }
      env {
        name  = "DB_USER"
        value = var.openmetadata_mysql_login
      }
      env {
        name        = "DB_USER_PASSWORD"
        secret_name = "mysql-password"
      }
      env {
        name  = "DB_HOST"
        value = var.openmetadata_mysql_fqdn
      }
      env {
        name  = "DB_PORT"
        value = "3306"
      }
      env {
        name  = "OM_DATABASE"
        value = "openmetadata_db"
      }
      env {
        name  = "ELASTICSEARCH_HOST"
        value = azurerm_container_app.openmetadata_elasticsearch.ingress[0].fqdn
      }
      env {
        name  = "ELASTICSEARCH_PORT"
        value = "443" # external ingress always terminates TLS on 443, proxying to the container's 9200 internally
      }
      env {
        name  = "ELASTICSEARCH_SCHEME"
        value = "https"
      }
      env {
        name  = "SERVER_HOST_API_URL"
        value = "https://ca-${var.name_prefix}-om-server.${azurerm_container_app_environment.main.default_domain}/api"
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8585
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
