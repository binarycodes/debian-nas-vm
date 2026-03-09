# MVP2: NAS Management API

## Overview

MVP1 establishes the NAS boot chain: image-baked config, boot-time render, and idempotent apply. Config changes require a full VM replacement cycle (new Packer image, Terraform destroy/create). The ZFS pool and data survive because disks are passed through by stable IDs.

MVP2 adds a management API running on the NAS VM so that day-2 operations (adding a dataset, creating an NFS export, provisioning a Samba share, configuring an iSCSI target) can be performed on a running system without VM replacement.

## Prerequisites

- MVP1 fully implemented and tested (boot chain, render, apply, all services).
- The idempotent render and apply scripts from MVP1 are the foundation — the API reuses the same Pydantic models, Jinja2 templates, and validate/render/apply logic directly.

## Goals

- Manage NAS resources (datasets, NFS exports, Samba shares, iSCSI targets, firewall rules) against a running VM without rebuilding.
- Maintain the same safety guarantees as MVP1: validation before apply, atomic config writes, idempotent operations, RFC1918 enforcement.
- Keep the MVP1 boot chain intact as the bootstrap and disaster-recovery path — the API is a day-2 overlay, not a replacement.
- `services.yml` remains the single source of truth at all times.

## Future Consumers — Design Constraint

The API will be consumed by at least two clients beyond direct HTTP use:

1. **MVP3 — Terraform provider**: A custom Terraform provider (`terraform-provider-cloudyhome`) will drive the API for IaC-managed day-2 operations. Terraform imposes specific requirements: resources must have stable identifiers, GET must return enough state to detect drift, errors must be machine-readable, and operations must be idempotent.

2. **Future — Web application**: A browser-based management UI will call the API directly. This requires consistent JSON response shapes, clear field-level error messages, and well-named resources that a human-readable UI can present without special-casing.

These constraints must be respected in every API design decision made in MVP2. The API is not a one-off script wrapper — it is a stable interface contract.

## Architecture

### State Ownership: API mutates services.yml

The API reads `services.yml`, applies changes in memory through the existing Pydantic models, re-renders, re-applies, and writes the updated `services.yml` back to disk after each successful operation. This means:

- The boot chain continues to work correctly after any VM rebuild — it always uses the current `services.yml`, which reflects all API-driven changes.
- No second state store or state file to reconcile.
- YAML comments in `services.yml` will not survive a roundtrip through Pydantic — this is acceptable; the file is machine-managed after first boot.
- The baked-in `services.yml` becomes the bootstrap seed only; after first boot the API owns it.

Disaster recovery: rebuild VM from image (which has the original baked `services.yml`) → boot chain runs → API clients (Terraform in MVP3, or operator-issued requests) re-converge the running state.

### Management API

A FastAPI application running on the NAS VM as a systemd service. It is a thin HTTP layer over the same validate → render → apply pipeline from MVP1.

Responsibilities:
- Accept declarative resource definitions (create/update/delete).
- Validate input using the same Pydantic models from MVP1.
- Acquire the shared render/apply lock (same `flock` as MVP1 scripts).
- Read and mutate `services.yml` in memory.
- Re-render affected templates using the same Jinja2 pipeline.
- Apply changes using the same apply logic (ZFS, Samba users, service reloads).
- Write the updated `services.yml` back atomically on success.
- Return current resource state in a consistent JSON shape suitable for both programmatic and UI consumption.

The API does **not** reinvent or duplicate logic — it imports from `cloudyhome.*` directly.

### Systemd Integration

A new service `cloudyhome-nas-api.service` starts after `cloudyhome-nas-apply.service` completes successfully. It holds the API server process. The shared lock file ensures the boot chain and API cannot run simultaneously: if the boot chain is running, the API will fail to acquire the lock and must wait; conversely, the API holds the lock only for the duration of each mutating request.

```
cloudyhome-nas-apply.service
        ↓
cloudyhome-nas-api.service  (FastAPI, always-running)
```

### Relationship to MVP1

- Fresh VM boot: MVP1 boot chain runs first. API starts after.
- Day-2 changes: go through API → `services.yml` updated → render/apply cycle.
- Disaster recovery: rebuild VM, boot chain uses latest `services.yml`, then clients re-converge.
- API unavailable: NFS/Samba/iSCSI continue serving (kernel and daemon level). The API is required only for config changes.

## API Design

### Authentication

Bearer token auth. The token is stored in `secrets.enc.yaml` at a known path (e.g., `api/token`) and resolved at API startup via the existing `secrets_context` / `resolve_ref` machinery. All requests must carry `Authorization: Bearer <token>`. Requests without a valid token receive 401.

### TLS

The API listens on HTTPS only. A self-signed TLS certificate and private key are generated at first boot (via `openssl` in the API's startup or a separate one-shot systemd service) and stored at a fixed path (e.g., `/etc/cloudyhome/api/tls.{crt,key}`). See questions.md for the cert strategy options.

### API Port

A dedicated management port (see questions.md for the exact port). A corresponding firewall rule entry must exist in `services.yml` to allow access from admin hosts. The operator is responsible for this rule (consistent with MVP1 explicit firewall philosophy).

### Response Shape Convention

All resource endpoints return a consistent JSON envelope so that both Terraform and the web UI can consume responses without special-casing:

- **Single resource** (GET one, POST, PUT): returns the full resource object as a flat JSON object.
- **Collection** (GET all): returns `{ "items": [ ... ] }`.
- **Errors**: returns `{ "error": "<code>", "message": "<human-readable>", "detail": [ ... ] }` where `detail` is an array of field-level error objects `{ "field": "<name>", "message": "<reason>" }` for validation failures.
- **Delete**: returns `204 No Content`.

Resource objects always include an `id` field (matching the URL path parameter) so clients can build links without reconstructing paths.

### Endpoints

All endpoints are under the `/v1/` prefix.

#### Datasets

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/datasets` | List all datasets |
| GET | `/v1/datasets/{key}` | Get dataset (config fields + live ZFS `used`/`available` from `zfs get`) |
| POST | `/v1/datasets` | Create dataset |
| PUT | `/v1/datasets/{key}` | Update dataset (quota change) |
| DELETE | `/v1/datasets/{key}` | Destroy dataset (blocked if referenced by any export, share, or iSCSI LUN) |

#### NFS Exports

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/nfs/exports` | List all NFS exports |
| GET | `/v1/nfs/exports/{name}` | Get export |
| POST | `/v1/nfs/exports` | Create export |
| PUT | `/v1/nfs/exports/{name}` | Update export |
| DELETE | `/v1/nfs/exports/{name}` | Delete export |

#### Samba Shares

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/samba/shares` | List all shares |
| GET | `/v1/samba/shares/{name}` | Get share |
| POST | `/v1/samba/shares` | Create share |
| PUT | `/v1/samba/shares/{name}` | Update share |
| DELETE | `/v1/samba/shares/{name}` | Delete share |

#### iSCSI Targets

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/iscsi/targets` | List all targets |
| GET | `/v1/iscsi/targets/{name}` | Get target |
| POST | `/v1/iscsi/targets` | Create target |
| PUT | `/v1/iscsi/targets/{name}` | Update target |
| DELETE | `/v1/iscsi/targets/{name}` | Delete target (blocked if LUN zvols are busy) |

#### Firewall Rules

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/firewall/rules` | List all rules |
| GET | `/v1/firewall/rules/{service}` | Get rule |
| POST | `/v1/firewall/rules` | Create rule |
| PUT | `/v1/firewall/rules/{service}` | Update rule |
| DELETE | `/v1/firewall/rules/{service}` | Delete rule |

#### System / Read-only

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/zpool` | Pool status, health, capacity, scrub state (live `zpool status`) |
| GET | `/v1/health` | API liveness check — no auth required, returns `{ "status": "ok" }` |

### Error Handling

| HTTP Status | Condition |
|-------------|-----------|
| 200 | Success, resource returned |
| 201 | Resource created |
| 204 | Resource deleted |
| 400 | Pydantic validation failure — field-level error detail included |
| 401 | Missing or invalid bearer token |
| 404 | Resource not found |
| 409 | Concurrent operation in progress (lock held); caller should retry |
| 422 | Cross-field validation failure (e.g., export path references a non-existent dataset) |
| 500 | Apply-phase failure; `services.yml` not written, running services not changed |

All non-2xx responses use the standard error envelope: `{ "error": "<code>", "message": "...", "detail": [...] }`.

### Locking

Every mutating request acquires the shared lock file (same path as `RENDER_LOCK` in MVP1) using non-blocking `flock`. If the lock is held (by another API request or by the boot chain), the API returns 409 immediately. The lock is held only for the duration of validate → read services.yml → mutate → render → apply → write services.yml. It is released before the response is returned.

The boot chain services use `LOCK_EX | LOCK_NB` (fail-fast), same as the existing scripts. The API uses the same semantics.

## Render/Apply Flow (API Path)

When the API receives a mutating request (POST/PUT/DELETE):

1. Authenticate bearer token.
2. Validate incoming payload against Pydantic schema.
3. Acquire exclusive lock (`flock`, non-blocking → 409 if busy).
4. Load current `services.yml` into a `NasConfig` object.
5. Apply the requested mutation in memory (add/update/remove the resource from the config).
6. Run cross-field validation on the mutated config (`validate_static`).
7. Decrypt secrets and run full validation (`validate_all`) — needed to confirm secret refs still resolve.
8. Re-render all affected config file(s) using the Jinja2 pipeline.
9. Apply changes:
   - ZFS: create/destroy datasets or zvols.
   - Users: add/remove Samba OS users and smbpasswd entries.
   - Services: reload/restart affected daemons (nfs-server, smbd, target, nftables).
10. Write the updated `services.yml` atomically (temp file + move).
11. Release lock.
12. Return new resource state.

On any failure at steps 6–10, the lock is released, `services.yml` is **not** written, and the running services are **not** restarted. The API returns 500 with a description. Re-running the operation is safe (idempotent).

## Resource Lifecycle Details

### Datasets

- POST creates the ZFS dataset (`zfs create -o quota=<q> <pool>/<name>`) and adds the entry to `services.yml`.
- PUT updates the quota (`zfs set quota=<q>`) and updates `services.yml`.
- DELETE: blocked if any NFS export `.path`, Samba share `.path`, or iSCSI LUN `.path` references this dataset's mountpoint or zvol path. Returns 422 with a description of the blocking dependencies. If unblocked, calls `zfs destroy`.
- See questions.md Q3 on path derivation.

### NFS Exports

- POST adds export to `services.yml`, re-renders `/etc/exports.d/cloudyhome.exports`, reloads `nfs-server` (`exportfs -ra`).
- DELETE removes from `services.yml`, re-renders exports, reloads.
- The export's `path` must reference a path belonging to an existing dataset in `services.yml` (cross-field check at step 6).

### Samba Shares

- POST adds share, creates OS user if new (`useradd`), sets smbpasswd, re-renders `smb.conf`, reloads `smbd`.
- DELETE removes share from `services.yml`. OS users are **not** deleted automatically (they may be referenced by other shares or own files). Re-renders smb.conf.
- The share's `path` must reference an existing dataset path.

### iSCSI Targets

- POST adds target, creates zvol(s) if they don't exist, regenerates `saveconfig.json` programmatically (reuses `build_saveconfig` from the render script), restarts `target`.
- DELETE: blocked if the LUN zvol is actively mapped. Returns 422. If unblocked, removes target from config, restarts `target`. Zvol is **not** automatically destroyed — see questions.md Q5.

### Firewall Rules

- POST adds rule, re-renders `nftables.conf`, validates with `nft -c`, reloads with `nft -f`.
- DELETE removes rule, re-renders, reloads.
- The management API port rule must always be present. See questions.md Q8 for enforcement strategy.

## Secrets Handling in the API

The API process decrypts `secrets.enc.yaml` into tmpfs at startup (reusing `secrets_context` from MVP1), resolves the API token, and holds the decrypted secrets in memory for the lifetime of the process. Each request that needs secret resolution calls `resolve_ref` against the already-decrypted secrets.

Secrets passed in API request bodies (e.g., Samba user passwords, CHAP credentials, NFS client CIDRs) are delivered as secret reference paths (`*_ref` fields), not plaintext values — same convention as `services.yml`. The actual values must already exist in `secrets.enc.yaml`. Adding a new secret requires updating and re-deploying `secrets.enc.yaml` out-of-band — see questions.md Q6.

## Out of Scope for MVP2

- **Terraform provider**: Deferred to MVP3. The API is designed to be consumed by Terraform, but the provider implementation is a separate MVP.
- **Web application**: Deferred to a future MVP. The API response shapes are designed with the web UI in mind.
- **Garage management**: Container lifecycle is complex. Garage config changes still require Packer rebuild.
- **FTP management**: Container-based, same situation as Garage.
- **Pool management**: `zpool create`, `zpool destroy`, disk replacement — operator-only via direct SSH.
- **Secrets rotation**: Updating `secrets.enc.yaml` at runtime — deferred.
- **API versioning**: A single `/v1/` prefix is sufficient; formal versioning strategy is deferred.

## Implementation Plan

### Phase 1: API Foundation

- Add FastAPI + uvicorn to the Python package dependencies.
- Implement startup routine: decrypt secrets into tmpfs, resolve API token.
- Implement shared lock acquisition wrapper (reuses `fcntl` logic from render scripts).
- Implement `GET /v1/health` (no auth required).
- Implement TLS cert generation at first boot (one-shot systemd service or API startup hook).
- Add `cloudyhome-nas-api.service` systemd unit (after `cloudyhome-nas-apply.service`).
- Add the management port to the firewall section in the example `services.yml`.

### Phase 2: Dataset and Firewall CRUD

- Implement dataset endpoints (GET list, GET one, POST, PUT, DELETE with dependency check).
- Implement firewall rule endpoints (GET list, GET one, POST, PUT, DELETE with pinned-rule guard).
- Wire both through the full mutate → validate → render → apply → write pipeline.
- Add tests (unit: model mutation logic; integration: against a running API with mocked ZFS).

### Phase 3: NFS and Samba CRUD

- Implement NFS export endpoints with path-to-dataset cross-field check.
- Implement Samba share endpoints with user provisioning logic.
- Add tests.

### Phase 4: iSCSI CRUD

- Implement iSCSI target endpoints.
- Reuse `build_saveconfig` from the render script.
- Implement zvol create with busy-check before delete.
- Add tests.

### Phase 5: Integration Testing and Documentation

- End-to-end test: boot chain → API start → API calls → verify services and `services.yml`.
- Update packer-checklist.md with API-related build steps (management port firewall rule, API token in secrets).
- OpenAPI schema review: confirm response shapes are clean for Terraform and web UI consumption.

## Open Questions

See `questions.md` for decisions that require input before implementation.
