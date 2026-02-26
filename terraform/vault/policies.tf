resource "vault_policy" "arecibo" {
  name = "arecibo"

  policy = <<-EOT
    path "secret/data/arecibo/*" {
      capabilities = ["read"]
    }
  EOT
}
