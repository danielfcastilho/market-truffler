# infra

Reserved for infrastructure that isn't owned by a single app — deployment
configuration, provisioning scripts, or environment-specific overlays.

## Status

- `keep-awake.sh` — prevents macOS idle sleep while the stack collects
  (`start` / `status` / `stop`); see `docs/OPERATIONS.md`.

Local development is otherwise covered by the root `docker-compose.yml` and
each app's own `Dockerfile`. No deployment target exists yet.
