variable "vault_secret_path" {
  description = "KVv2 secret path under mount 'secret' used by arecibo runtime."
  type        = string
  default     = "arecibo/config"
}

variable "arecibo_api_keys" {
  description = "Comma-separated API keys accepted by X-API-Key. Leave null to generate."
  type        = string
  default     = null
  sensitive   = true
}
