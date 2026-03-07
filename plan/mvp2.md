# MVP2: NAS Management API and Terraform Provider

## Overview

MVP1 establishes the NAS boot chain: image-baked config, boot-time render, and idempotent apply. Config changes require a full VM replacement cycle (new Packer image, Terraform destroy/create). The ZFS pool and data survive because disks are passed through by stable IDs.

MVP2 adds a management API running on the NAS VM and a custom Terraform provider, so that day-2 operations (adding a dataset, creating an NFS export, provisioning a Samba share, configuring an iSCSI target) can be performed on a running system without VM replacement.

## Prerequisites

- MVP1 fully implemented and tested (boot chain, render, apply, all services).
- The idempotent render and apply scripts from MVP1 are the foundation — the API reuses or wraps the same logic.

## Goals

- Manage NAS resources (datasets, NFS exports, Samba shares, iSCSI targets) from Terraform against a running VM.
- Maintain the same safety guarantees as MVP1: validation before apply, atomic config writes, idempotent operations.
- Keep the MVP1 boot chain intact as the bootstrap/recovery path — the API is a day-2 overlay, not a replacement for boot-time provisioning.

## Architecture

### Management API

A lightweight HTTP API running on the NAS VM. Responsible for:

- Accepting declarative resource definitions (create/update/delete).
- Validating input using the same Pydantic schemas from MVP1.
- Rendering config files using the same Jinja2 templates from MVP1.
- Applying changes (dataset creation, service reload) using the same logic from `nas-apply-config`.
- Returning current state for Terraform read/plan operations.

### Terraform Provider

A custom Terraform provider (`terraform-provider-cloudyhome`) that talks to the management API. Exposes NAS resources as Terraform resource types.

### Relationship to MVP1

- The MVP1 boot chain remains the initial provisioning and disaster recovery path.
- On first boot, the system comes up via the MVP1 flow (services.yml + secrets → render → apply).
- After boot, the API takes over for ongoing management.
- The API writes to the same config files and manages the same services as the boot chain.

## Design Questions

These are the open questions to resolve before implementation.

### State Ownership

MVP1 uses `services.yml` as the single source of truth, baked into the image. With an API:

- **Option A**: The API mutates `services.yml` on disk. Simple, single source of truth, but the running VM's config diverges from the baked image. Next VM rebuild would need to incorporate the live state or risk regression.
- **Option B**: The API maintains its own state store (SQLite, JSON file on zpool) and `services.yml` becomes the bootstrap-only seed. The API is authoritative after first boot. Cleaner separation but two state systems.
- **Option C**: Terraform is the source of truth. The API is stateless — it receives the full desired state on each apply and converges. State lives in Terraform state file only. Simplest API, but requires Terraform to always send complete config.

### Secrets Delivery

MVP1 has secrets baked as SOPS-encrypted files with the AGE key delivered via cloud-init. The API needs a way to receive secrets over the wire:

- TLS on the API is mandatory (secrets in transit).
- Authentication mechanism for the API itself (mTLS, API token, etc.).
- How Terraform passes sensitive values (Samba passwords, CHAP creds, NFS client CIDRs) — Terraform `sensitive` attributes map naturally to this.

### Concurrency and Locking

MVP1 uses `flock` for render and apply serialization. The API needs the same:

- Serialize all mutating API calls (only one render/apply cycle at a time).
- Return appropriate errors (409 Conflict or retry-after) if a concurrent operation is in progress.
- Ensure the boot chain and API cannot run simultaneously (shared lock file).

### Terraform Provider Resource Granularity

- **Per-resource type**: `cloudyhome_dataset`, `cloudyhome_nfs_export`, `cloudyhome_samba_share`, `cloudyhome_iscsi_target`, `cloudyhome_firewall_rule`. Most Terraform-native — each resource has its own lifecycle, can be created/destroyed independently, supports `depends_on` and references between resources.
- **Single resource**: `cloudyhome_nas_config` that takes the full config as a block. Simpler provider, but coarse — any change replaces the whole config. Loses Terraform's per-resource plan granularity.
- Per-resource is the better fit for Terraform's model.

### Firewall Integration

Adding an NFS export may require a corresponding firewall rule update:

- **Explicit**: Firewall rules are a separate Terraform resource. The operator is responsible for keeping firewall rules consistent with services (same as MVP1).
- **Implicit**: The API automatically adds/removes firewall rules when services are created/destroyed. Less operator burden, but magic behavior that's harder to reason about.
- Explicit is more consistent with MVP1's design (Section 14.4: "Firewall rules are literal... it is the operator's responsibility to keep firewall rules consistent with service ports").

### API Technology

- Python (FastAPI or similar) is the natural choice — the MVP1 scripts are already Python, the Pydantic models and Jinja2 templates can be directly imported.
- Runs as a systemd service on the NAS VM, started after the MVP1 boot chain completes.
- Listens on a dedicated port, restricted by firewall to admin hosts only.

### Terraform Provider Technology

- Written in Go (standard for Terraform providers, uses the Terraform Plugin Framework).
- Communicates with the management API over HTTPS.
- Published as a local provider or private registry provider.

## Resource Types (Preliminary)

| Terraform Resource | API Endpoint | Maps To |
|---|---|---|
| `cloudyhome_dataset` | `POST/GET/PUT/DELETE /v1/datasets/{name}` | `zfs create`, `zfs set quota`, `zfs destroy` |
| `cloudyhome_nfs_export` | `POST/GET/PUT/DELETE /v1/nfs/exports/{name}` | `/etc/exports.d/cloudyhome.exports` + reload |
| `cloudyhome_samba_share` | `POST/GET/PUT/DELETE /v1/samba/shares/{name}` | `/etc/samba/smb.conf` + user provisioning + reload |
| `cloudyhome_iscsi_target` | `POST/GET/PUT/DELETE /v1/iscsi/targets/{name}` | `/etc/target/saveconfig.json` + restart |
| `cloudyhome_firewall_rule` | `POST/GET/PUT/DELETE /v1/firewall/rules/{service}` | `/etc/nftables.conf` + reload |

Data sources for read-only queries:

| Terraform Data Source | API Endpoint | Returns |
|---|---|---|
| `cloudyhome_dataset` | `GET /v1/datasets/{name}` | Current dataset properties (quota, used, mountpoint) |
| `cloudyhome_zpool` | `GET /v1/zpool` | Pool status, health, capacity |

## Render and Apply Flow (API Path)

When the API receives a mutating request:

1. Validate input against Pydantic schema (same models as MVP1).
2. Acquire exclusive lock (`flock`, same as MVP1).
3. Update the authoritative state store.
4. Re-render affected config file(s) using Jinja2 templates (same templates as MVP1).
5. Apply changes: create datasets, provision users, reload/restart services (same logic as MVP1).
6. Release lock.
7. Return new state to caller.

The key principle: the API is a thin HTTP layer over the same validate → render → apply pipeline from MVP1.

## Bootstrap and Recovery

- **Fresh VM boot**: MVP1 boot chain runs. `services.yml` provides the initial config. API starts after boot chain completes.
- **Disaster recovery**: Rebuild VM from image + IaC. MVP1 boot chain restores services. Terraform state still has the resource definitions — `terraform apply` converges any drift between the bootstrap config and the desired state.
- **API unavailable**: The NAS continues serving (NFS/Samba/iSCSI are kernel/daemon-level services). The API is only needed for config changes, not for data path operation.

## Open Questions

- How does `zfs destroy` work safely? Datasets backing active NFS exports or Samba shares should not be destroyable. The API needs dependency checking.
- Should the API support Garage and FTP management, or keep those as boot-time-only (container lifecycle is more complex)?
- Backup/export of API state for migration or audit purposes.
- API versioning strategy for forward compatibility.
- Whether the provider should live in the same repo or a separate one.
