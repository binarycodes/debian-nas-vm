# MVP3: Terraform Provider

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Goals](#goals)
- [Architecture](#architecture)
  - [Technology](#technology)
  - [Provider Configuration](#provider-configuration)
  - [Resource Types](#resource-types)
  - [Data Sources](#data-sources)
  - [Per-Resource State Model](#per-resource-state-model)
  - [Error Handling and Retries](#error-handling-and-retries)
  - [Sensitive Fields](#sensitive-fields)
  - [Resource Dependencies](#resource-dependencies)
- [Bootstrap and Recovery](#bootstrap-and-recovery)
  - [Fresh VM (First Terraform Apply)](#fresh-vm-first-terraform-apply)
  - [Ongoing Day-2 Changes](#ongoing-day-2-changes)
  - [Disaster Recovery](#disaster-recovery)
- [Out of Scope for MVP3](#out-of-scope-for-mvp3)
- [Implementation Plan](#implementation-plan)
  - [Phase 1: Provider Scaffold](#phase-1-provider-scaffold)
  - [Phase 2: Dataset and Firewall Resources](#phase-2-dataset-and-firewall-resources)
  - [Phase 3: NFS, Samba, and Users Resources](#phase-3-nfs-samba-and-users-resources)
  - [Phase 4: iSCSI Resources](#phase-4-iscsi-resources)
  - [Phase 5: Singleton Resources](#phase-5-singleton-resources)
  - [Phase 6: Data Sources, Testing, and Documentation](#phase-6-data-sources-testing-and-documentation)
- [Open Questions](#open-questions)

## Overview

MVP2 delivers the NAS management API backed by SQLite. MVP3 wraps that API with a custom Terraform provider (`terraform-provider-cloudyhome`) so that day-2 NAS operations can be expressed as infrastructure-as-code and managed alongside the rest of the homelab stack.

## Prerequisites

- MVP2 fully implemented and the API stable (endpoint shapes, error codes, response envelopes).
- The OpenAPI schema from MVP2 is the contract. The provider is built against it.
- A running NAS VM with the API accessible on port 9443 from the machine running Terraform.

## Goals

- Express all NAS resources as Terraform resources, covering every MVP2 API endpoint.
- Support `terraform import` so that resources created by the bootstrap config can be brought under Terraform management without re-creating them.
- Support `terraform plan` drift detection: read live state from the API and diff against declared config.
- Maintain the same safety guarantees as the API: Terraform never bypasses validation.

## Architecture

### Technology

- Written in Go.
- Uses the [Terraform Plugin Framework](https://github.com/hashicorp/terraform-plugin-framework) (not SDKv2, which is legacy).
- Communicates with the management API over HTTPS on port 9443.

### Provider Configuration

```hcl
provider "cloudyhome" {
  endpoint     = "https://10.0.0.10:9443"
  token        = var.cloudyhome_api_token
  tls_cert_pem = file("nas-api.crt")  # or insecure_skip_verify = true for dev
}
```

### Resource Types

All resource types map directly to MVP2 API endpoints. Values are inline (no `_ref` fields — the API accepts actual values).

#### Collection Resources

| Terraform Resource | API Endpoint | Key Field | Notes |
|---|---|---|---|
| `cloudyhome_dataset` | `/v1/datasets/{key}` | `key` | `quota` updatable; `path` set at create |
| `cloudyhome_nfs_export` | `/v1/nfs/exports/{name}` | `name` | Clients list with inline CIDRs, fully replaced on update |
| `cloudyhome_samba_share` | `/v1/samba/shares/{name}` | `name` | References users by username |
| `cloudyhome_samba_user` | `/v1/samba/users/{username}` | `username` | Password is plaintext over TLS, sensitive |
| `cloudyhome_iscsi_target` | `/v1/iscsi/targets/{name}` | `name` | LUNs, initiators, inline CHAP credentials |
| `cloudyhome_firewall_rule` | `/v1/firewall/rules/{service}` | `service` | Inline source IPs/CIDRs |
| `cloudyhome_ftp_user` | `/v1/ftp/users/{username}` | `username` | Password is plaintext over TLS, sensitive |

#### Singleton Resources

Singleton resources represent global configuration. They always exist (at most one instance). Terraform `create` maps to API POST (or PUT if already present). `delete` removes the config from the API.

| Terraform Resource | API Endpoint | Notes |
|---|---|---|
| `cloudyhome_samba_global` | `/v1/samba` | workgroup, server_string, min_protocol |
| `cloudyhome_iscsi_global` | `/v1/iscsi` | base_iqn, portal_port, dataset |
| `cloudyhome_garage` | `/v1/garage` | Full Garage config including secrets (admin_token, rpc_secret) |
| `cloudyhome_ftp` | `/v1/ftp` | FTP service config (image, ports, dirs) |
| `cloudyhome_alerting` | `/v1/alerting` | SMTP config, email addresses, credentials |
| `cloudyhome_host` | `/v1/host` | Host IP, disk IDs |
| `cloudyhome_general` | `/v1/cloudyhome/general` | allowed_email_domains |

### Data Sources

| Terraform Data Source | API Endpoint | Returns |
|---|---|---|
| `data.cloudyhome_dataset` | `GET /v1/datasets/{key}` | Quota, used, available, mountpoint |
| `data.cloudyhome_zpool` | `GET /v1/zpool` | Live health, capacity, scrub state |
| `data.cloudyhome_pools` | `GET /v1/pools` | Pool names and dataset inventory |
| `data.cloudyhome_host` | `GET /v1/host` | Host IP, disk IDs |

### Per-Resource State Model

Terraform state stores the full resource definition as returned by the API GET. On `terraform plan`, the provider reads current state from the API (GET) and diffs against the Terraform config. On `terraform apply`, it calls POST/PUT/DELETE as appropriate.

The provider always does a GET before applying, so `terraform import` is optional — if a resource already exists when POST is attempted, the API returns `409 CONFLICT` and the provider reads current state via GET and switches to a PUT. However, explicit `terraform import` is supported for all resource types to seed clean state without any side effects.

### Error Handling and Retries

The provider maps MVP2's error code catalog to Terraform-appropriate behavior:

| API Error Code | HTTP Status | Provider Behavior |
|---|---|---|
| `VALIDATION_FAILED` | 400 | Fail immediately, surface field-level detail to user |
| `AUTH_REQUIRED` | 401 | Fail immediately |
| `NOT_FOUND` | 404 | On Read: mark resource as gone (triggers re-create). On Delete: succeed (already gone). |
| `CONFLICT` | 409 | On Create: read existing resource, diff, update if needed |
| `LOCK_CONTENTION` | 409 | Retry with exponential backoff (configurable max retries, default 5) |
| `DEPENDENCY_BLOCKED` | 422 | Fail immediately, surface blocking dependency detail |
| `APPLY_FAILED` | 500 | Retry once, then fail |
| `INTERNAL_ERROR` | 500 | Fail immediately |
| `NOT_READY` | 503 | Retry with backoff (API still starting up) |
| `OPERATION_TIMEOUT` | 504 | Retry once, then fail |

The provider distinguishes `CONFLICT` from `LOCK_CONTENTION` using the `error` field in the response envelope (both are HTTP 409 but have different error codes).

### Sensitive Fields

Fields containing secrets are marked `Sensitive: true` in the Terraform schema so they don't appear in plan output or state diffs:

- `cloudyhome_samba_user.password`
- `cloudyhome_ftp_user.password`
- `cloudyhome_iscsi_target.auth.chap_secret`
- `cloudyhome_garage.admin_token`
- `cloudyhome_garage.rpc_secret`
- `cloudyhome_alerting.smtp_password`
- Provider config `token`

### Resource Dependencies

Standard Terraform `depends_on` and attribute references:

```hcl
resource "cloudyhome_dataset" "media" {
  key   = "shares_media"
  path  = "/zpool0/shares/media"
  quota = "4T"
}

resource "cloudyhome_samba_user" "alice" {
  username = "smb_alice"
  password = var.smb_alice_password
}

resource "cloudyhome_samba_share" "media" {
  name       = "media"
  path       = cloudyhome_dataset.media.path
  users      = [cloudyhome_samba_user.alice.username]
  depends_on = [cloudyhome_dataset.media, cloudyhome_samba_user.alice]
}

resource "cloudyhome_nfs_export" "media" {
  name = "media"
  path = cloudyhome_dataset.media.path
  clients {
    cidrs   = ["10.0.0.0/24"]
    options = ["rw", "sync", "no_subtree_check"]
    identity_map {
      mode = "root_squash"
    }
  }
  depends_on = [cloudyhome_dataset.media]
}

resource "cloudyhome_firewall_rule" "nfs" {
  service  = "nfs"
  ports    = [2049]
  proto    = ["tcp"]
  sources  = ["10.0.0.0/24"]
}
```

## Bootstrap and Recovery

### Fresh VM (First Terraform Apply)

1. MVP1 boot chain bakes `services.yml` + `secrets.enc.yaml` into the image.
2. On first boot, the bootstrap service imports YAML + decrypts SOPS → populates SQLite → deletes YAML files.
3. Boot chain renders and applies from SQLite. API starts.
4. `terraform import` seeds state for resources already present (from bootstrap config). Future applies are no-ops for those.
5. `terraform apply` creates any additional resources declared in Terraform but not in the bootstrap config.

### Ongoing Day-2 Changes

`terraform apply` → provider calls API → API mutates SQLite, renders, applies, commits.

### Disaster Recovery

1. Rebuild VM from Packer image (original bootstrap `services.yml` + `secrets.enc.yaml`).
2. First boot: bootstrap imports into SQLite, deletes YAML. Boot chain renders/applies.
3. `terraform apply` — provider detects drift (resources in Terraform state that don't exist or differ from API state) and re-creates/updates via the API.
4. Running state matches Terraform state again.

For faster recovery, an automation pipeline can pull config exports (`GET /v1/config/services` + `GET /v1/config/secrets`) after each change and bake them into the next Packer image. This minimizes the drift Terraform needs to reconcile after a rebuild.

## Out of Scope for MVP3

- **Web application**: Deferred to a future MVP.
- **Pool-level Terraform resources**: `zpool create`, `zpool destroy` — too dangerous for IaC; operator-only via SSH.
- **Config export Terraform resources**: Export endpoints are for backup/rebake automation, not Terraform-managed resources.

## Implementation Plan

### Phase 1: Provider Scaffold

- Initialize Go module and provider scaffold with Terraform Plugin Framework.
- Implement provider config: `endpoint`, `token`, `tls_cert_pem`, `insecure_skip_verify`.
- Implement HTTP client wrapper (auth header, TLS, error parsing from API error envelope, retry logic for `LOCK_CONTENTION` / `NOT_READY` / `OPERATION_TIMEOUT`).
- Implement `GET /v1/health` as a provider connectivity check at `terraform init` / `terraform validate`.

### Phase 2: Dataset and Firewall Resources

- Implement `cloudyhome_dataset` resource (Create, Read, Update, Delete, Import).
- Implement `cloudyhome_firewall_rule` resource (CRUD + Import).
- These are the simplest resources — good proof-of-concept for the full lifecycle.
- Add acceptance tests against a running API.

### Phase 3: NFS, Samba, and Users Resources

- Implement `cloudyhome_nfs_export` (inline CIDRs, clients list).
- Implement `cloudyhome_samba_global` (singleton).
- Implement `cloudyhome_samba_share` (user references by username).
- Implement `cloudyhome_samba_user` (sensitive password field).
- Add acceptance tests for each.

### Phase 4: iSCSI Resources

- Implement `cloudyhome_iscsi_global` (singleton).
- Implement `cloudyhome_iscsi_target` (LUNs, inline CHAP, initiators).
- Add acceptance tests.

### Phase 5: Singleton Resources

- Implement `cloudyhome_garage` (full config + secrets).
- Implement `cloudyhome_ftp` (service config).
- Implement `cloudyhome_ftp_user` (CRUD).
- Implement `cloudyhome_alerting` (SMTP config + secrets).
- Implement `cloudyhome_host` (IP, disk IDs).
- Implement `cloudyhome_general` (allowed_email_domains).
- Add acceptance tests.

### Phase 6: Data Sources, Testing, and Documentation

- Implement `data.cloudyhome_dataset`, `data.cloudyhome_zpool`, `data.cloudyhome_pools`, `data.cloudyhome_host`.
- End-to-end test: boot chain → API start → `terraform apply` → verify services and SQLite state.
- Validate disaster recovery path: destroy VM, rebuild, `terraform apply` re-converges.
- Validate import path: bootstrap resources → `terraform import` → `terraform plan` shows no changes.
- Document provider configuration, example Terraform modules, and import procedures.

## Open Questions

### Q1: TLS Cert in Terraform Provider Config

The API generates a self-signed cert at first boot (unless a cert is injected via Packer/cloud-init). The Terraform provider must be configured to trust it. Options:

- `tls_cert_pem = file("nas-api.crt")` — pinned cert PEM, loaded from a local file.
- `insecure_skip_verify = true` — skip verification (dev/home-use only).
- Inject a stable cert via Packer so it's known at Terraform config time.

### Q2: Provider Repo Location

Should `terraform-provider-cloudyhome` live in:
- **This repo** (monorepo, Go code alongside Python) — simpler to develop and test together.
- **A separate repo** — standard Terraform provider convention, cleaner module boundaries, easier to publish to a registry later.

Monorepo is fine for MVP3. A separate repo can be split out later if needed.
