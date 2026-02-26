output "arecibo_role_id" {
  description = "AppRole role_id for arecibo runtime."
  value       = vault_approle_auth_backend_role.arecibo.role_id
  sensitive   = true
}

output "arecibo_secret_path" {
  description = "Vault KV path for arecibo runtime config."
  value       = "secret/${var.vault_secret_path}"
}
