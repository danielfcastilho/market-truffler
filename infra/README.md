# infra

Reserved for infrastructure that isn't owned by a single app — deployment
configuration, provisioning scripts, or environment-specific overlays.

## Status

Empty in the foundation milestone. Local development is fully covered by the
root `docker-compose.yml` and each app's own `Dockerfile`. This directory
exists as a boundary for later, more elaborate infrastructure (e.g. a
production deployment target) rather than being populated speculatively now.
