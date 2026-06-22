# Runbook — verified-deploy gate (Janus-signed images)

Operate the fail-closed deploy gate that replaces Watchtower for a signature-gated
service. Background: [`../architecture/image-signing.md`](../architecture/image-signing.md).

## Enable the gate for a service

1. Key + policy exist in Janus (`img-<svc>`) — see image-signing.md §setup.
2. Pin the public key on the host:
   ```bash
   VAULT_ADDR=https://janus.sz3yan.com VAULT_TOKEN=$MF_TOKEN \
     cosign public-key --key hashivault://img-mainframe > keys/img-mainframe.pub
   ```
3. Drop `com.centurylinklabs.watchtower.enable=true` from that service in
   `docker-compose.yml` and `docker compose up -d` it once.
4. Schedule the gate (host crontab / `~/bin/szejo-infra-startup.sh`):
   ```cron
   */2 * * * * cd /path/to/szejo-control-plane && python3 -m scripts.szejo secrets run -- \
     python3 -m scripts.szejo deploy verify szejo-control-plane-mainframe ghcr.io/sz3yan/mainframe keys/img-mainframe.pub
   ```

## Verify it works

```bash
# Manual run — should verify the running digest and report "up to date".
python3 -m scripts.szejo secrets run -- python3 -m scripts.szejo deploy verify
tail -n 20 logs/verified_update.log
```

## Negative test (prove it fails closed)

Point the gate at an unsigned/foreign image, or corrupt `keys/img-mainframe.pub`:
the run logs `REFUSING UPDATE … cosign verify failed` and exits non-zero; the
running container is untouched. Restore the correct key afterward.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `cosign verify failed` on a real image | CI sign step skipped (still `continue-on-error`?) or key/policy missing in Janus. Check the workflow run + `transit.sign` audit events in Janus. |
| `pinned public key missing` | Re-run the `cosign public-key` pin step. |
| `docker login ghcr.io failed` | Run under `szejo secrets` so `GITHUB_TOKEN` is injected. |
| Image deploys without verification | Service still has the Watchtower label — remove it. |
