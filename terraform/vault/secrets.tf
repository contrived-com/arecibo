resource "random_password" "arecibo_api_key" {
  length  = 48
  special = false
}

locals {
  runtime_api_keys = coalesce(var.arecibo_api_keys, random_password.arecibo_api_key.result)
}

resource "vault_kv_secret_v2" "arecibo_config" {
  mount = "secret"
  name  = var.vault_secret_path

  data_json = jsonencode({
    arecibo_api_keys = local.runtime_api_keys
  })

  lifecycle {
    # Allow manual key rotation in Vault without Terraform drift enforcement.
    ignore_changes = [data_json]
  }
}
