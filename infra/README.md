# infra

Reserved for infrastructure that isn't owned by a single app — deployment
configuration, provisioning scripts, or environment-specific overlays.

## Status

- `keep-awake.sh` — prevents macOS idle sleep while the stack collects:
  a per-user launchd agent (`start` / `status` / `stop`, opt-in
  `enable-at-login`) that reports its state to `var/host-status/` for
  Vitals; see `docs/OPERATIONS.md`.

Local development is otherwise covered by the root `docker-compose.yml` and
each app's own `Dockerfile`. No deployment target exists yet.
