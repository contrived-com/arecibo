# Arecibo Vault Bootstrap

This module defines Arecibo runtime secrets and AppRole policy in Vault.

- KV path: `secret/arecibo/config`
- Runtime field: `arecibo_api_keys`
- AppRole: `arecibo`
- Policy: read access to `secret/data/arecibo/*`

## Apply

```bash
cd terraform/vault
export VAULT_ADDR=https://vault.concordia.contrived.com:8200
export VAULT_TOKEN=...
terraform init
terraform apply
```

After apply, set service `.env` pointers only:

- `VAULT_ADDR`
- `VAULT_ROLE_ID`
- `VAULT_SECRET_ID`
- `ARECIBO_VAULT_PATH=arecibo/config`
- `ARECIBO_API_KEYS_FIELD=arecibo_api_keys`
