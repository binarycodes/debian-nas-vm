# MVP2: NAS Management API

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Goals](#goals)
- [Future Consumers — Design Constraint](#future-consumers--design-constraint)
- [Architecture](#architecture)
  - [State Ownership: SQLite as Runtime Source of Truth](#state-ownership-sqlite-as-runtime-source-of-truth)
  - [Management API](#management-api)
  - [Systemd Integration](#systemd-integration)
  - [Relationship to MVP1](#relationship-to-mvp1)
- [API Design](#api-design)
  - [Authentication](#authentication)
  - [TLS](#tls)
  - [API Port](#api-port)
  - [Response Shape Convention](#response-shape-convention)
  - [Endpoints](#endpoints)
  - [Error Handling](#error-handling)
  - [Locking](#locking)
- [Render/Apply Flow (API Path)](#renderapply-flow-api-path)
- [Resource Lifecycle Details](#resource-lifecycle-details)
  - [Datasets](#datasets)
  - [NFS Exports](#nfs-exports)
  - [Samba Shares](#samba-shares)
  - [iSCSI Targets](#iscsi-targets)
  - [Firewall Rules](#firewall-rules)
  - [Samba Users](#samba-users)
  - [iSCSI Global](#iscsi-global)
  - [FTP Users](#ftp-users)
  - [Host Config](#host-config)
  - [S3](#s3)
  - [FTP](#ftp)
  - [Health Alerts](#health-alerting)
- [Secrets Handling in the API](#secrets-handling-in-the-api)
- [Model Layer: Inline Values Only](#model-layer-inline-values-only)
- [Config Export: Reconstructing YAML + SOPS from SQLite](#config-export-reconstructing-yaml--sops-from-sqlite)
- [Out of Scope for MVP2](#out-of-scope-for-mvp2)
- [Implementation Plan](#implementation-plan)
  - [Phase 0: Extract Python Package](#phase-0-extract-python-package)
  - [Phase 1: SQLite and API Foundation](#phase-1-sqlite-and-api-foundation)
  - [Phase 2: Dataset and Firewall CRUD](#phase-2-dataset-and-firewall-crud)
  - [Phase 3: NFS, Samba, and Samba Users CRUD](#phase-3-nfs-samba-and-samba-users-crud)
  - [Phase 4: iSCSI CRUD](#phase-4-iscsi-crud)
  - [Phase 5: S3, FTP, Health, and Host Config](#phase-5-s3-ftp-health-and-host-config)
  - [Phase 6: Integration Testing and Documentation](#phase-6-integration-testing-and-documentation)
- [Open Questions](#open-questions)

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
- A SQLite database at `/var/lib/cloudyhome/db` is the single source of truth at runtime.
- Extract the `cloudyhome` Python package into a standalone `cloudyhome-nas` project (publishable to PyPI, installable via pip) to establish a clean separation between library code, CLI entry points, and the OS filesystem layout.

## Future Consumers — Design Constraint

The API will be consumed by at least two clients beyond direct HTTP use:

1. **MVP3 — Terraform provider**: A custom Terraform provider (`terraform-provider-cloudyhome`) will drive the API for IaC-managed day-2 operations. Terraform imposes specific requirements: resources must have stable identifiers, GET must return enough state to detect drift, errors must be machine-readable, and operations must be idempotent.

2. **Future — Web application**: A browser-based management UI will call the API directly. This requires consistent JSON response shapes, clear field-level error messages, and well-named resources that a human-readable UI can present without special-casing.

These constraints must be respected in every API design decision made in MVP2. The API is not a one-off script wrapper — it is a stable interface contract.

## Architecture

### State Ownership: SQLite as Runtime Source of Truth

A SQLite database at `/var/lib/cloudyhome/db` is the persistent source of truth for all NAS configuration and secrets.

**First boot (bootstrap):**
1. The boot chain detects no SQLite DB exists.
2. It imports `services.yml` and decrypts `secrets.enc.yaml` into a new SQLite database.
3. A marker is written after the first successful render/apply from the DB.
4. The bootstrap files (`services.yml` and `secrets.enc.yaml`) are **deleted** — they are no longer needed.

**Subsequent boots:**
1. The boot chain detects the SQLite DB (and marker) exists.
2. It reads configuration and secrets directly from SQLite — no YAML, no SOPS.
3. Render and apply proceed as normal using Pydantic models loaded from SQLite.

**Day-2 mutations:**
- The API reads from and writes to SQLite. No YAML files are involved at runtime.
- All secret values (passwords, CHAP keys, tokens, CIDRs) are stored as plaintext in the SQLite DB. This is acceptable — the SOPS-encrypted YAML was only needed because secrets were baked into the VM image. At runtime, the API receives plaintext secrets over TLS directly.

**Config export (for backup / rebake):**
- `GET /v1/backup/services` generates `services.yml` on-the-fly from SQLite and serves it. No file is written to disk.
- `GET /v1/backup/secrets` generates `secrets.enc.yaml` on-the-fly from SQLite, encrypted with SOPS using an age public key supplied by the caller in the `X-Encryption-Key` header. No file is written to disk.
- An automation pipeline pulls these after changes and feeds them into the next Packer build, where they become the bootstrap seed for a new VM.

**Disaster recovery:** Rebuild VM from image (which has the baked `services.yml` + `secrets.enc.yaml`) → first boot imports into SQLite → API clients (Terraform in MVP3, or operator requests) re-converge any remaining state.

**Schema migrations:** The SQLite DB includes a `schema_version` integer. At startup (both bootstrap and API), the current version is checked. If the DB version is older than the code version, migration functions run in order (e.g., v1→v2, v2→v3) inside a transaction before any other operation. Migrations use `ALTER TABLE ADD COLUMN` for additive changes; for destructive changes (column removal, rename), the migration recreates the table. This ensures that a Packer image upgrade with new schema fields works seamlessly against an existing DB on a running VM.

### Management API

A FastAPI application running on the NAS VM as a systemd service. It is a thin HTTP layer over the same validate → render → apply pipeline from MVP1.

Responsibilities:
- Accept declarative resource definitions (create/update/delete).
- Validate input using the same Pydantic models from MVP1.
- Acquire the shared render/apply lock (same `flock` as MVP1 scripts).
- Read and mutate the SQLite database.
- Load the mutated state into Pydantic models.
- Re-render affected templates using the same Jinja2 pipeline.
- Apply changes using the same apply logic (ZFS, Samba users, service reloads).
- Commit the SQLite transaction on success.
- Return current resource state in a consistent JSON shape suitable for both programmatic and UI consumption.

The API does **not** reinvent or duplicate logic — it imports from `cloudyhome.*` directly.

**Process model:** The API runs as a **single Uvicorn worker**. This keeps the `flock`-based locking model simple (one process = one set of file descriptors) and is sufficient for a home NAS workload.

**Startup requirements:** The API **refuses to start** if the SQLite database cannot be opened or is corrupt. The systemd service fails, providing a clean signal to monitoring. There is no degraded mode.

### Systemd Integration

The boot chain is extended with a bootstrap service that runs before everything else. On first boot, it imports YAML+SOPS into SQLite and deletes the source files. On subsequent boots, it's a no-op (DB already exists). All downstream services read exclusively from SQLite — no dual code paths.

```
cloudyhome-nas-bootstrap.service  (first boot: YAML + SOPS → SQLite, delete YAML; subsequent boots: no-op)
        ↓
cloudyhome-nas-validate.service   (validate config from SQLite)
        ↓
cloudyhome-nas-render.service     (render templates from SQLite)
        ↓
cloudyhome-nas-apply.service      (apply rendered configs)
        ↓
cloudyhome-nas-api.service        (FastAPI, always-running)
```

The shared lock files ensure the boot chain and API cannot run simultaneously: if the boot chain is running, the API will fail to acquire the lock and must wait; conversely, the API holds the lock only for the duration of each mutating request.

### Relationship to MVP1

- First boot: bootstrap service imports YAML + SOPS into SQLite, deletes the YAML files. Boot chain then renders/applies from SQLite. API starts after.
- Subsequent boots: boot chain reads directly from SQLite. No YAML, no SOPS.
- Day-2 changes: go through API → SQLite updated → render/apply cycle.
- Disaster recovery: rebuild VM from image (with baked YAML + SOPS) → first boot bootstraps SQLite → API clients re-converge.
- API unavailable: NFS/Samba/iSCSI continue serving (kernel and daemon level). The API is required only for config changes.

## API Design

### Authentication

Bearer token auth. The token is stored in the SQLite database (imported from `secrets.enc.yaml` at bootstrap). All requests must carry `Authorization: Bearer <token>`. Requests without a valid token receive 401.

**Token rotation:** `PUT /v1/auth/token` replaces the current bearer token. Requires the current token for authentication. The caller must update the Terraform provider config and any automation scripts after rotation.

**Auth boundary:** Only `GET /v1/health` (liveness) and `GET /v1/ready` (readiness) are unauthenticated. All other endpoints — including read-only ones like `/v1/zpools` and the config download endpoints — require a valid bearer token.

### TLS

The API listens on HTTPS only. TLS cert and key are stored at `/etc/cloudyhome/api/tls.{crt,key}`. A one-shot systemd service (`cloudyhome-nas-tls.service`) runs before `cloudyhome-nas-api.service` and generates a self-signed cert with `openssl req -x509` **only if the files are not already present**. Operators who want a stable cert (e.g., for the Terraform provider or web UI) can inject a cert/key pair via Packer or cloud-init at that path; the service will detect it and skip generation.

### API Port

The API listens on port **9443**. A corresponding firewall rule must be configured to allow access from admin hosts. The operator is responsible for this rule (consistent with MVP1 explicit firewall philosophy).

### Response Shape Convention

All resource endpoints return a consistent JSON envelope so that both Terraform and the web UI can consume responses without special-casing:

- **Single resource** (GET one, POST, PUT): returns the full resource object as a flat JSON object.
- **Collection** (GET all): returns `{ "items": [ ... ] }`. Collections are sorted alphabetically by name (or primary key). No pagination — resource counts are small on a home NAS.
- **Errors**: returns `{ "error": "<CODE>", "message": "<human-readable>", "detail": [ ... ] }` where `error` is a machine-readable error code (see [Error Handling](#error-handling)), and `detail` is an array of field-level error objects `{ "field": "<name>", "message": "<reason>" }` for validation failures.
- **Delete**: returns `204 No Content`.

Resource objects always include an `id` field (matching the URL path parameter) so clients can build links without reconstructing paths.

**Path vs body identifier rule:** The URL path parameter is the sole resource identifier. Request bodies for PUT must **not** include an `id`/`name` field — if present, the API returns a validation error. This avoids mismatch ambiguity.

**Create idempotency:** POST to an already-existing resource returns `409 Conflict`. Clients must use PUT to update existing resources.

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

#### Samba Global

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/samba` | Get Samba global settings (workgroup, server_string, min_protocol) |
| PUT | `/v1/samba` | Update Samba global settings. Re-renders `smb.conf`, reloads `smbd`. |

#### Samba Users

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/samba/users` | List Samba OS users, including which shares reference them |
| GET | `/v1/samba/users/{username}` | Get user details and share references |
| POST | `/v1/samba/users` | Create OS user and set Samba password (plaintext over TLS, hashed server-side) |
| PUT | `/v1/samba/users/{username}` | Update Samba password |
| DELETE | `/v1/samba/users/{username}` | Delete OS user (blocked if any share still references the user; returns 422) |

#### iSCSI Global

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/iscsi` | Get iSCSI global settings (base_iqn, portal_port, dataset) |
| PUT | `/v1/iscsi` | Update iSCSI global settings. Re-renders `saveconfig.json`, restarts `target`. |

#### FTP Users

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/ftp/users` | List FTP users |
| GET | `/v1/ftp/users/{username}` | Get FTP user |
| POST | `/v1/ftp/users` | Create FTP user (plaintext password over TLS) |
| PUT | `/v1/ftp/users/{username}` | Update FTP user password |
| DELETE | `/v1/ftp/users/{username}` | Delete FTP user |

#### General Config

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/general` | Get general settings (allowed_email_domains) |
| PUT | `/v1/general` | Update general settings |

#### Host Config

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/host` | Get host config (IP address, disk IDs) |
| PUT | `/v1/host` | Update host config |

#### S3

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/s3` | Get S3 config (singleton, currently backed by Garage) |
| POST | `/v1/s3` | Create S3 config (409 if already exists) |
| PUT | `/v1/s3` | Update S3 config |
| DELETE | `/v1/s3` | Remove S3 config (stops and removes Quadlet) |

#### FTP

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/ftp` | Get FTP config (singleton) |
| POST | `/v1/ftp` | Create FTP config (409 if already exists) |
| PUT | `/v1/ftp` | Update FTP config |
| DELETE | `/v1/ftp` | Remove FTP config (stops and removes Quadlet) |

#### Health Alerts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/alerts` | Get health alert config (singleton) |
| POST | `/v1/alerts` | Create health alert config (409 if already exists) |
| PUT | `/v1/alerts` | Update health alert config |
| DELETE | `/v1/alerts` | Remove health alert config (disables email alerting; smartd/ZED continue logging to journal) |

#### Backup Export

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/backup/services` | Generate and download `services.yml` from SQLite on-the-fly (auth required, `Content-Type: application/x-yaml`). No file is written to disk. |
| GET | `/v1/backup/secrets` | Generate and download `secrets.enc.yaml` from SQLite on-the-fly, encrypted with SOPS using the age public key in the `X-Encryption-Key` request header (auth required, `Content-Type: application/octet-stream`). Returns 400 if header is missing. No file is written to disk. |

#### Auth

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/v1/auth/token` | Rotate the bearer token. Request body: `{ "token": "<new-token>" }`. Requires current token for auth. |

#### System / Read-only

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/zpools` | List all pools with live status, health, capacity, scrub state (from `zpool status`) |
| GET | `/v1/zpools/{name}` | Live status for a specific pool |
| GET | `/v1/health` | Liveness check — no auth required, returns `{ "status": "ok" }`. Reports only that the process is running. |
| GET | `/v1/ready` | Readiness check — no auth required. Returns `{ "status": "ready" }` when the SQLite DB is open, config is loaded, and the API can process mutations. Returns 503 otherwise. |

### Error Handling

All non-2xx responses use the standard error envelope: `{ "error": "<CODE>", "message": "...", "detail": [...] }`. The `error` field is a machine-readable code from the catalog below, designed for deterministic retry/abort decisions by Terraform and other automation.

#### Error Code Catalog

| HTTP Status | Error Code | Condition | Retryable? |
|-------------|------------|-----------|------------|
| 200 | — | Success, resource returned | — |
| 201 | — | Resource created | — |
| 204 | — | Resource deleted | — |
| 400 | `VALIDATION_FAILED` | Pydantic validation failure — field-level error detail included | No |
| 401 | `AUTH_REQUIRED` | Missing or invalid bearer token | No |
| 404 | `NOT_FOUND` | Resource not found | No |
| 409 | `CONFLICT` | Resource already exists (POST to existing) | No (use PUT) |
| 409 | `LOCK_CONTENTION` | Concurrent operation in progress (lock held) | Yes |
| 422 | `DEPENDENCY_BLOCKED` | Cross-field validation failure (e.g., export references non-existent dataset, delete blocked by dependents) | No |
| 500 | `APPLY_FAILED` | Apply-phase failure (render/service restart); SQLite transaction rolled back. Partial side effects possible (best-effort, no rollback of system state — ephemeral VM; re-bake on persistent failure). | Yes (transient) |
| 500 | `INTERNAL_ERROR` | Unexpected error | Maybe |
| 503 | `NOT_READY` | API is not yet ready to process mutations (SQLite not loaded, config not parsed) | Yes |
| 504 | `OPERATION_TIMEOUT` | Mutation exceeded the configured timeout | Yes |

### Locking

Every mutating request acquires both shared lock files (`RENDER_LOCK` and `APPLY_LOCK` from MVP1) using non-blocking `flock`. Locks must always be acquired in a fixed canonical order: **`RENDER_LOCK` first, then `APPLY_LOCK`**. This order is enforced by a shared helper function that all code paths must use — never acquire locks individually. If either lock is held (by another API request or by the boot chain), the API returns `409 LOCK_CONTENTION` immediately and releases any lock already acquired. Both locks are held for the duration of validate → read SQLite → mutate → render → apply → commit SQLite, and are released before the response is returned.

The boot chain services use `LOCK_EX | LOCK_NB` (fail-fast), same as the existing scripts. The API uses the same semantics.

**Timeout:** Each mutating request is subject to a configurable timeout (default: 60 seconds). If the full validate → render → apply → write cycle does not complete within the timeout, the operation is aborted, locks are released, and the API returns `504 OPERATION_TIMEOUT`. The timeout is configurable via an environment variable or API config file.

## Render/Apply Flow (API Path)

When the API receives a mutating request (POST/PUT/DELETE):

1. Authenticate bearer token.
2. Validate incoming payload against Pydantic schema.
3. Acquire exclusive lock (`flock`, non-blocking → 409 if busy).
4. Begin SQLite transaction.
5. Apply the requested mutation to the database.
6. Load the mutated state into Pydantic models (`NasConfig`).
7. Run cross-field validation on the mutated config (`validate_static`).
8. Run full validation (`validate_all`) with secrets resolved directly from SQLite — no `_ref` indirection, values are inline.
9. Re-render all affected config file(s) using the Jinja2 pipeline.
10. Apply changes:
    - ZFS: create/destroy datasets or zvols.
    - Users: add/remove Samba OS users and smbpasswd entries.
    - Services: reload/restart affected daemons (nfs-server, smbd, target, nftables).
11. Commit SQLite transaction.
12. Release lock.
13. Return new resource state.

On any failure at steps 7–10, the SQLite transaction is rolled back and locks are released. However, partial side effects are possible at step 10 (e.g., a dataset may have been created before a service reload fails). There is no rollback of system state — this is best-effort apply. Since the NAS runs on an ephemeral VM, persistent failures are resolved by re-baking. Re-running the operation is safe (idempotent).

## Resource Lifecycle Details

### Datasets

- POST creates the ZFS dataset (`zfs create -o quota=<q> <pool>/<name>`) and adds the entry to SQLite.
- PUT updates the quota (`zfs set quota=<q>`) and updates SQLite.
- DELETE: blocked if any NFS export `.path`, Samba share `.path`, or iSCSI LUN `.path` references this dataset's mountpoint or zvol path. Returns 422 with a description of the blocking dependencies. If unblocked, calls `zfs destroy`.
- The caller provides the full path explicitly in the POST body (e.g., `{ "key": "media", "path": "/zpool0/media", "quota": "500G" }`).

### NFS Exports

- POST adds export to SQLite, re-renders `/etc/exports.d/cloudyhome.exports`, reloads `nfs-server` (`exportfs -ra`). Request body includes actual CIDRs and options (not `cidr_ref`).
- DELETE removes from SQLite, re-renders exports, reloads.
- The export's `path` must reference a path belonging to an existing dataset (cross-field check at step 7).

### Samba Shares

- POST adds share, re-renders `smb.conf`, reloads `smbd`. All referenced users must already exist (created via `POST /v1/samba/users`); returns 422 `DEPENDENCY_BLOCKED` otherwise.
- DELETE removes share from SQLite. OS users are **not** deleted automatically (they may be referenced by other shares or own files). Re-renders smb.conf.
- The share's `path` must reference an existing dataset path.

### iSCSI Targets

- POST adds target, creates zvol(s) if they don't exist, regenerates `saveconfig.json` programmatically (reuses `build_saveconfig` from the render script), restarts `target`.
- DELETE: blocked if the LUN zvol is actively mapped. Returns 422. If unblocked, removes target from config, restarts `target`. Zvol is **not** automatically destroyed — a separate `DELETE /v1/datasets/{key}` call is required.

### Firewall Rules

- POST adds rule, re-renders `nftables.conf`, validates with `nft -c`, reloads with `nft -f`. Request body accepts inline source IPs/CIDRs (not `sources_ref`).
- DELETE removes rule, re-renders, reloads.
- The management API port firewall rule is the operator's responsibility. No enforcement or pinning — the operator must ensure the API port is open from admin hosts. Consistent with MVP1's explicit firewall philosophy.

### Samba Users

- POST creates an OS user (`useradd`) and sets the Samba password via `smbpasswd`. The request body includes `{ "username": "...", "password": "..." }` — the password is plaintext over TLS, hashed server-side. Samba uses NT hashes internally (MD4 of UTF-16LE password); the API handles this derivation.
- PUT updates the Samba password for an existing user. Same request format as POST.
- DELETE removes the OS user (`userdel`) and the Samba password entry. Blocked if any Samba share still references the user (returns 422 `DEPENDENCY_BLOCKED`). No file ownership check — orphaned files retain the numeric UID; the operator is responsible for chown before deletion.
- Samba share creation (`POST /v1/samba/shares`) requires all referenced users to already exist. If a user does not exist, the request returns 422 `DEPENDENCY_BLOCKED`. Create users first via `POST /v1/samba/users`.

### iSCSI Global

- GET returns global iSCSI settings (`base_iqn`, `portal_port`, `dataset`).
- PUT updates global settings. Re-renders `saveconfig.json` for all targets, restarts `target` service. `dataset` must match a path in an existing dataset.

### FTP Users

- POST creates an FTP user. Request body includes `{ "username": "...", "password": "..." }` (plaintext over TLS). Stored in SQLite; re-renders `ftp.env`, restarts FTP container.
- PUT updates the FTP user password. Re-renders `ftp.env`, restarts FTP container.
- DELETE removes the FTP user. Re-renders `ftp.env`, restarts FTP container.

### Host Config

- Singleton resource. GET returns current host IP and disk IDs.
- PUT updates host IP and/or disk IDs in SQLite. Re-renders all configs that reference the host IP (nftables, S3/Garage, iSCSI portal, etc.) and reloads affected services. Disk IDs are stored for the ZFS import boot-time check.
- Host IP must be a valid RFC1918 address (same global IP policy as MVP1).

### S3

- Singleton resource. The S3 endpoint abstracts the underlying implementation (currently Garage). POST creates the full S3 config (image, ports, directories, secrets). PUT updates it. DELETE removes it.
- POST/PUT: re-renders `garage.toml` and the Quadlet `.container` file, reloads systemd (`daemon-reload`), starts/restarts the Garage container.
- DELETE: stops the Garage container, removes the Quadlet file, reloads systemd. Does not destroy data directories.
- Request body includes actual secret values (`admin_token`, `rpc_secret`) — stored as plaintext in SQLite.
- `enabled` field controls whether the container is started. Config is always rendered regardless of `enabled` (consistent with MVP1).

### FTP

- Singleton resource. POST creates the full FTP config. PUT updates it. DELETE removes it.
- POST/PUT: re-renders `ftp.env` and the Quadlet `.container` file, reloads systemd, starts/restarts the FTP container.
- DELETE: stops the FTP container, removes the Quadlet file, reloads systemd.
- Request body includes actual FTP user credentials — stored as plaintext in SQLite.

### Health Alerts

- Singleton resource. POST creates alert config. PUT updates it. DELETE removes it.
- POST/PUT: re-renders `alert.conf` and `/etc/msmtprc`, reloads no services (alert script reads config at invocation time).
- DELETE: re-renders `alert.conf` with `ALERT_ENABLED=false`, removes `/etc/msmtprc`. smartd and ZED continue logging to journal.
- Request body includes SMTP credentials and email addresses as plaintext — stored in SQLite.
- All email addresses are subject to the global email domain policy. `allowed_email_domains` is stored in SQLite and validated on mutation.

## Secrets Handling in the API

All secret values (passwords, CHAP keys, API tokens, CIDRs, SMTP credentials) are stored as **plaintext in the SQLite database**. This is acceptable because:

- The SOPS-encrypted YAML approach was designed for secrets baked into VM images. At runtime, the API receives plaintext values over TLS directly.
- If an attacker has filesystem access to the SQLite DB, they already have root on the NAS.

**Bootstrap:** On first boot, `secrets.enc.yaml` is decrypted via SOPS and imported into SQLite alongside `services.yml`. Both files are deleted after successful import. SOPS and the decryption key are only needed at bootstrap time.

**API requests:** Secret values are passed as plaintext fields in request bodies (e.g., `"password": "..."` for users, `"chap_secret": "..."` for iSCSI). No `_ref` indirection — the API does not depend on `secrets.enc.yaml` or SOPS at runtime.

**Secret fields in GET responses:** All secret fields are **omitted** from GET responses. These are write-only — accepted in POST/PUT but never returned. This applies to: `samba_user.password`, `ftp_user.password`, `iscsi_target.chap_secret`, `s3.admin_token`, `s3.rpc_secret`, `alerts.smtp_password`. No drift detection on any secret field — out-of-band changes are at the operator's own risk.

**Export:** `GET /v1/backup/secrets` generates SOPS-encrypted YAML on-the-fly using the age public key supplied in the `X-Encryption-Key` header. SOPS binary is required on the VM for this export path. The age public key can only encrypt (not decrypt), so leaking it is not a security risk.

## Model Layer: Inline Values Only

MVP1's Pydantic models use `_ref` fields resolved against decrypted secrets. With the SQLite architecture, this indirection is eliminated at runtime.

The **bootstrap import script** is a standalone one-time operation that parses `services.yml`, decrypts `secrets.enc.yaml`, resolves all `_ref` fields, and inserts the resulting inline values into SQLite. It does not need to share Pydantic models with the API — it's just an import/ETL script.

The **Pydantic models** used by the API and boot chain only ever see inline values from SQLite. No `_ref` fields, no dual-mode. For example, `POST /v1/nfs/exports` accepts `"clients": [{"cidrs": ["10.0.0.0/24"], ...}]`; `POST /v1/firewall/rules` accepts `"sources": ["10.0.0.0/24"]`.

## Config Export: Reconstructing YAML + SOPS from SQLite

The export endpoints must reconstruct the original `services.yml` + `secrets.enc.yaml` split from SQLite's flat data. This requires:

1. **A field-level classification** of which values are "secret" and which are "config". This classification is already implicit in MVP1's schema (fields with `_ref` suffixes are secret; everything else is config).
2. **Deterministic ref path generation**: for each secret field, generate a stable ref path (e.g., firewall source IPs for the "ssh" rule → `firewall/ssh`; Samba user "smb_alice" → `samba/users/smb_alice`). The path scheme must match MVP1's `secrets.enc.yaml` structure so that exported files are compatible with a fresh bootstrap.
3. **YAML generation**: emit `services.yml` with `_ref` fields and `secrets.enc.yaml` with the actual values at the corresponding paths. Then SOPS-encrypt the secrets file using the caller-supplied age public key.

The classification and ref path scheme are derived from the existing MVP1 secrets mapping contract (Section 14.5 of `mvp1.md`) and do not need new design — they just need to be implemented as a reverse mapping.

## Out of Scope for MVP2

- **Terraform provider**: Deferred to MVP3. The API is designed to be consumed by Terraform, but the provider implementation is a separate MVP.
- **Web application**: Deferred to a future MVP. The API response shapes are designed with the web UI in mind.
- **Pool management**: `zpool create`, `zpool destroy`, disk replacement — operator-only via direct SSH.
- **Secrets upload**: No endpoint to upload `secrets.enc.yaml`. Secrets are managed individually through resource endpoints (users, iSCSI, etc.).
- **API versioning**: A single `/v1/` prefix is sufficient; formal versioning strategy is deferred.
- **Correlation IDs**: `X-Request-ID` header and log correlation — deferred. Rely on systemd journal timestamps for now.
- **Pagination**: Not needed — home NAS resource counts are small. Stable ordering by name is sufficient.
- **Fault injection testing**: Not required for MVP2. Rely on unit tests with mocked failures and e2e happy-path tests.

## Implementation Plan

### Phase 0: Extract Python Package

Move the `cloudyhome` Python package out of `nas_root/` into a standalone project that can be published to PyPI and installed via pip.

- Create `cloudyhome-nas/` at the repo root with `pyproject.toml` and `src/cloudyhome/` layout.
- Move `nas_root/usr/local/lib/cloudyhome/cloudyhome/*.py` → `cloudyhome-nas/src/cloudyhome/`.
- Create `cloudyhome-nas/src/cloudyhome/cmd/` and move each Python sbin script's `main()` (plus helpers) into a dedicated module:
  - `nas-render-config` → `cloudyhome.cmd.render`
  - `nas-apply-config` → `cloudyhome.cmd.apply`
  - `nas-validate-config` → `cloudyhome.cmd.validate`
  - `nas-validate-install-phase` → `cloudyhome.cmd.validate_install`
  - `nas-garage-bootstrap` → `cloudyhome.cmd.garage`
- Promote `build_saveconfig` and `build_context` from `nas-render-config` into `cloudyhome.render` (not into `cloudyhome.cmd.render`) so the API can import them as library functions without depending on a cmd module.
- Declare `[project.scripts]` entry points in `pyproject.toml` (pip installs these to `/usr/local/bin/`). Add `fastapi`, `uvicorn[standard]`, and `aiosqlite` (or stdlib `sqlite3`) to dependencies now so they are present from Phase 1 onward.
- Delete `nas_root/usr/local/lib/cloudyhome/` and the five Python sbin scripts.
- Update the four affected systemd unit `ExecStart` paths from `/usr/local/sbin/` to `/usr/local/bin/` (`nas-validate-config`, `nas-render-config`, `nas-apply-config`, `nas-garage-bootstrap`). Bash scripts (`nas-zfs-import`, `nas-health-alert`, `nas-zedlet-wrapper`) stay in `/usr/local/sbin/` unchanged.
- Update `packaging/build.sh`: build a wheel from `cloudyhome-nas/` and copy it into the staging area under `/usr/share/cloudyhome/`.
- Update `nas_root/var/lib/cloudyhome/installer/Makefile`: `pip-install` target installs from `/usr/share/cloudyhome/cloudyhome_nas-*.whl`; remove Python scripts from the `permissions` target.
- **Migration risk:** No compatibility shims or transition bridge. If the package install fails mid-migration, the fix is to re-bake the VM — acceptable for an ephemeral VM deployment model.

### Phase 1: SQLite and API Foundation

- Define SQLite schema covering all resource types (datasets, NFS exports, Samba shares, iSCSI targets, firewall rules, users, host config, S3, FTP, health alerts) and all secret values (passwords, tokens, CIDRs, CHAP keys, SMTP credentials, email addresses, disk IDs, allowed email domains).
- **Define Pydantic models with inline values only** — no `_ref` fields. These are used by the API and boot chain. E.g., firewall rules have `"sources": ["10.0.0.0/24"]`; NFS exports have `"clients": [{"cidrs": [...]}]`; iSCSI targets have `"chap_secret": "..."`.
- Implement bootstrap import script (standalone, does not share models with the API): parse `services.yml` + decrypt `secrets.enc.yaml` → resolve all `_ref` fields → insert inline values into SQLite → write marker → delete YAML files.
- Implement `cloudyhome-nas-bootstrap.service` to run the import script on first boot (no-op if DB exists).
- **Modify all boot chain scripts** (`nas-validate-config`, `nas-render-config`, `nas-apply-config`, `nas-garage-bootstrap`, `nas-zfs-import`) to read from SQLite instead of YAML+SOPS. No dual code path needed — the bootstrap service always runs first and guarantees the SQLite DB exists before any other boot chain script executes.
- Implement shared lock acquisition wrapper (acquires `RENDER_LOCK` then `APPLY_LOCK` in that fixed order; reuses `fcntl` logic from render scripts). All code paths must use this helper — never acquire locks individually.
- Implement configurable mutation timeout (default 60s, via environment variable).
- Implement `GET /v1/health` (liveness, no auth required).
- Implement `GET /v1/ready` (readiness, no auth — checks SQLite open, config loaded).
- Implement config export endpoints with ref/secret reconstruction: `GET /v1/backup/services` (generate YAML with `_ref` fields from SQLite) and `GET /v1/backup/secrets` (generate SOPS-encrypted YAML with actual values from SQLite, using `X-Encryption-Key` header). The ref path scheme follows MVP1's secrets mapping contract.
- Implement `cloudyhome-nas-tls.service` (one-shot, before the API service): generate self-signed cert with `openssl req -x509` only if `/etc/cloudyhome/api/tls.{crt,key}` are not already present.
- Add `cloudyhome-nas-api.service` systemd unit (after `cloudyhome-nas-apply.service`; `ExecStart=/usr/local/bin/nas-api`).
- Add the management port to the firewall in the bootstrap example config.

### Phase 2: Dataset and Firewall CRUD

- Implement dataset endpoints (GET list, GET one, POST, PUT, DELETE with dependency check).
- Implement firewall rule endpoints (GET list, GET one, POST, PUT, DELETE — no pinned-rule enforcement, operator responsibility).
- Wire both through the full SQLite mutate → validate → render → apply → commit pipeline.
- Add tests (unit: model mutation logic; integration: against a running API with mocked ZFS).

### Phase 3: NFS, Samba, and Samba Users CRUD

- Implement NFS export endpoints with path-to-dataset cross-field check. Request bodies accept inline CIDRs and options (not `cidr_ref`).
- Implement Samba global settings endpoints (`GET /v1/samba`, `PUT /v1/samba`).
- Implement Samba share endpoints. Request bodies accept user references by username (not `users_ref` paths).
- Implement Samba user endpoints (`/v1/samba/users`): full CRUD (`POST`, `GET`, `PUT`, `DELETE`). Delete blocked if shares still reference the user (returns 422). No file ownership checks.
- Add tests.

### Phase 4: iSCSI CRUD

- Implement iSCSI global settings endpoints (`GET /v1/iscsi`, `PUT /v1/iscsi`).
- Implement iSCSI target endpoints. Request bodies accept inline CHAP credentials (not `chap_secret_ref`).
- Reuse `build_saveconfig` from `cloudyhome.render` (promoted there in Phase 0).
- Implement zvol create with busy-check before delete.
- Add tests.

### Phase 5: S3, FTP, Health, and Host Config

- Implement host config endpoints (`GET /v1/host`, `PUT /v1/host`). Mutations re-render all configs referencing host IP.
- Implement S3 singleton CRUD (`/v1/s3`). POST/PUT re-renders `garage.toml` + Quadlet, manages container lifecycle.
- Implement FTP singleton CRUD. POST/PUT re-renders `ftp.env` + Quadlet, manages container lifecycle.
- Implement FTP user endpoints (`/v1/ftp/users`): full CRUD. Changes re-render `ftp.env`, restart FTP container.
- Implement health alerts singleton CRUD. POST/PUT re-renders `alert.conf` + `msmtprc`.
- Implement general config endpoints (`GET/PUT /v1/general`) for `allowed_email_domains`.
- Implement read-only `GET /v1/zpools` and `GET /v1/zpools/{name}` (live pool status from `zpool status`).
- Add tests.

### Phase 6: Integration Testing and Documentation

- End-to-end test: first boot (YAML bootstrap → SQLite) → subsequent boot (SQLite only) → API calls → verify services and SQLite state.
- End-to-end test: config export → re-import on fresh VM → verify state matches (round-trip validation that exported YAML+SOPS produces identical SQLite state).
- End-to-end test: all boot chain scripts work from SQLite (validate, render, apply, zfs-import, garage-bootstrap).
- Update packer-checklist.md with API-related build steps (management port firewall rule, API token in secrets, SOPS key for bootstrap).
- OpenAPI schema review: confirm response shapes are clean for Terraform and web UI consumption.

## Open Questions

None. All decisions are recorded inline in this document.
