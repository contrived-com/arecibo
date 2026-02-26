resource "vault_approle_auth_backend_role" "arecibo" {
  backend        = "approle"
  role_name      = "arecibo"
  token_policies = [vault_policy.arecibo.name]
  token_ttl      = 3600
  token_max_ttl  = 14400
}
