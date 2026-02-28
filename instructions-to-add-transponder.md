# Instructions To Add Transponder

Use this playbook when integrating the Arecibo transponder into another service repo (example target: `~/dev/divining-rod`).

## TL;DR

Adding the transponder is **not** build-action-only.

- You usually **must** update one or more Dockerfiles.
- You may need compose/env wiring updates.
- GitHub Actions often needs little or no change if it already builds the modified Dockerfiles.

The easiest pattern is to consume the prebuilt artifact image:

- `ghcr.io/contrived-com/arecibo-transponder:prod`

and copy `/opt/transponder` into the target image.

---

## What You Add In A Service Repo

## 1) Dockerfile changes (required)

For each container that should run the transponder (typically Python API/worker/scraper, not static web):

1. Add a transponder artifact stage:

```dockerfile
FROM ghcr.io/contrived-com/arecibo-transponder:prod AS transponder
```

2. Copy runtime + launcher into final stage:

```dockerfile
COPY --from=transponder /opt/transponder /opt/transponder
COPY --from=transponder /opt/transponder/transponder/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
```

3. Set entrypoint to launcher and keep app command as CMD:

```dockerfile
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "src.main"]
```

Important:
- Keep the app command as `CMD` so it remains the primary application process (PID 1).
- Do not replace CMD with the transponder command.

---

## 2) Runtime env wiring (usually required)

The transponder needs collector endpoint and auth:

- `TRANSPONDER_COLLECTOR_URL` or `TRANSPONDER_COLLECTOR_CANDIDATES`
- `TRANSPONDER_API_KEY`
- identity hints like `TRANSPONDER_SERVICE_NAME`, `TRANSPONDER_ENVIRONMENT`, etc.

Current default collector candidates in transponder code are:
- `http://arecibo-api:8080`
- `https://arecibo.contrived.com`

### Vault-first environments

If the target service uses pointer-only `.env` (recommended), then:

- `.env` should only contain Vault pointers/approle data (`VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID`).
- App-level secret values should live in Vault and be fetched at runtime.

For `TRANSPONDER_API_KEY`, use the same strategy as other app secrets in that repo:
- fetch from Vault in startup/bootstrap logic, then export/inject to process env.

Do **not** commit raw API keys.

---

## 3) Docker Compose updates (sometimes required)

If not already present:
- join `concordia` network for Vault access
- keep host bindings on `127.0.0.1`

If the transponder should prefer internal route to Arecibo API, ensure network path to `arecibo-api` is available (same host shared network or explicit route).

---

## 4) GitHub Actions workflow (often no change)

If workflow already builds/pushes the Dockerfiles you edited, no workflow changes are required.

You only need workflow edits when:
- adding a new image target,
- changing dockerfile path/context,
- needing new build args/secrets.

For `divining-rod`, current workflow already builds `api/Dockerfile`, `worker/Dockerfile`, and `scraper/Dockerfile`, so Dockerfile edits alone usually flow through CI.

---

## 5) What to change in `divining-rod` specifically

Recommended transponder-enabled containers:
- `divining-rod-api`
- `divining-rod-worker`
- `divining-rod-scraper` (if long-running or event-emitting and useful)

Likely skip:
- `divining-rod-web` (Node web frontend) unless there is a clear reason.

Steps:
1. Edit `api/Dockerfile`, `worker/Dockerfile`, and optionally `scraper/Dockerfile` with the `arecibo-transponder` copy + entrypoint pattern.
2. Ensure compose env includes needed `TRANSPONDER_*` vars for those services.
3. Keep Vault pointer-only policy and fetch `TRANSPONDER_API_KEY` via Vault runtime path.
4. Push, watch CI, verify containers healthy.

---

## 6) Verification checklist

Inside running container:
- `/opt/transponder/.venv/bin/transponder` exists
- `/entrypoint.sh` exists and executable

Runtime:
- app process is still healthy
- transponder process is running (child/background)
- Arecibo API shows announce/heartbeat traffic

Quick checks:
- target service local health endpoint still green
- Arecibo `/health` green
- logs show successful `announce` and periodic `heartbeat`

---

## 7) Common failure modes

- Missing `ENTRYPOINT ["/entrypoint.sh"]` -> transponder never starts.
- Overwriting CMD incorrectly -> app no longer PID 1.
- Missing `TRANSPONDER_API_KEY` -> announce/policy calls rejected.
- Wrong Vault address/network -> cannot fetch key.
- Forcing localhost collector where unreachable -> connection failures.

---

## 8) Reference contract

Arecibo transponder artifact image:
- `ghcr.io/contrived-com/arecibo-transponder:prod`

Launcher defaults:
- `TRANSPONDER_BIN=/opt/transponder/.venv/bin/transponder`

Arecibo external endpoint:
- `https://arecibo.contrived.com`
