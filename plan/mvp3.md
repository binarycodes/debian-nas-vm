# MVP3: Terraform Provider

## Overview

MVP2 delivers the NAS management API. MVP3 wraps that API with a custom Terraform provider (`terraform-provider-cloudyhome`) so that day-2 NAS operations can be expressed as infrastructure-as-code and managed alongside the rest of the homelab stack.

## Prerequisites

- MVP2 fully implemented and the API stable (endpoint shapes, error codes, response envelopes).
- The OpenAPI schema from MVP2 is the contract. The provider is built against it.
- A running NAS VM with the API accessible from the machine running Terraform.

## Goals

- Express NAS resources (datasets, NFS exports, Samba shares, iSCSI targets, firewall rules) as Terraform resources.
- Support `terraform import` so that resources created by the MVP1 boot chain can be brought under Terraform management without re-creating them.
- Support `terraform plan` drift detection: read live state from the API and diff against declared config.
- Maintain the same safety guarantees as the API: Terraform never bypasses validation.

## Architecture

### Technology

- Written in Go.
- Uses the [Terraform Plugin Framework](https://github.com/hashicorp/terraform-plugin-framework) (not SDKv2, which is legacy).
- Communicates with the management API over HTTPS.
- Provider config accepts `endpoint`, `token`, and TLS verification settings (see questions.md Q2 for cert strategy).

### Provider Configuration

```hcl
provider "cloudyhome" {
  endpoint     = "https://10.0.0.10:9090"
  token        = var.cloudyhome_api_token
  tls_cert_pem = file("nas-api.crt")  # or insecure_skip_verify = true for dev
}
```

### Resource Types

| Terraform Resource | API Endpoint | Notes |
|---|---|---|
| `cloudyhome_dataset` | `/v1/datasets/{key}` | `quota` is the only updatable field |
| `cloudyhome_nfs_export` | `/v1/nfs/exports/{name}` | Clients list fully replaced on update |
| `cloudyhome_samba_share` | `/v1/samba/shares/{name}` | `users_ref`, paths, masks |
| `cloudyhome_iscsi_target` | `/v1/iscsi/targets/{name}` | LUNs, initiators, auth |
| `cloudyhome_firewall_rule` | `/v1/firewall/rules/{service}` | Ports, proto, sources_ref |

### Data Sources

| Terraform Data Source | API Endpoint | Returns |
|---|---|---|
| `data.cloudyhome_dataset` | `GET /v1/datasets/{key}` | Quota, used, available, mountpoint |
| `data.cloudyhome_zpool` | `GET /v1/zpool` | Health, capacity, scrub state |

### Per-Resource State Model

Terraform state stores the full resource definition as returned by the API GET. On `terraform plan`, the provider reads current state from the API (GET) and diffs against the Terraform config. On `terraform apply`, it calls POST/PUT/DELETE as appropriate.

The provider always does a GET before applying, so `terraform import` is optional — if a resource already exists when POST is attempted, the provider can detect the 409/conflict and switch to a PUT. However, explicit `terraform import` is supported for all resource types to seed clean state without any side effects.

### Resource Dependencies

Standard Terraform `depends_on` and attribute references:

```hcl
resource "cloudyhome_dataset" "media" {
  key   = "media"
  quota = "500G"
}

resource "cloudyhome_nfs_export" "media" {
  name = "media"
  path = cloudyhome_dataset.media.mountpoint
  depends_on = [cloudyhome_dataset.media]
}
```

## Bootstrap and Recovery

### Fresh VM (First Terraform Apply)

1. MVP1 boot chain runs: `services.yml` (baked into image) → render → apply. NFS/Samba/iSCSI come up per the bootstrap config.
2. API starts.
3. `terraform import` seeds state for resources already present (from bootstrap config). Future applies are no-ops for those.
4. `terraform apply` creates any additional resources declared in Terraform but not in the bootstrap config.

### Ongoing Day-2 Changes

`terraform apply` → provider calls API → API mutates `services.yml`, renders, applies, writes `services.yml`.

### Disaster Recovery

1. Rebuild VM from Packer image (original bootstrap `services.yml`).
2. MVP1 boot chain restores services per bootstrap config.
3. `terraform apply` — provider detects drift (resources in Terraform state that don't exist or differ from API state) and re-creates/updates via the API.
4. Running state matches Terraform state again.

See questions.md Q4 on whether `services.yml` should be stored on the zpool to short-circuit the re-convergence step.

## Out of Scope for MVP3

- **Web application**: Deferred to a future MVP.
- **Garage or FTP Terraform resources**: These are not managed by the API in MVP2; nothing to wrap.
- **Pool-level Terraform resources**: Too dangerous; operator-only.

## Implementation Plan

### Phase 1: Provider Scaffold

- Initialize Go module and provider scaffold with Terraform Plugin Framework.
- Implement provider config: `endpoint`, `token`, `tls_cert_pem`, `insecure_skip_verify`.
- Implement HTTP client wrapper (auth header, TLS, error parsing from API error envelope).
- Implement `GET /v1/health` as a provider connectivity check at `terraform init` / `terraform validate`.

### Phase 2: Dataset Resource

- Implement `cloudyhome_dataset` resource (Create, Read, Update, Delete, Import).
- This is the simplest resource — good proof-of-concept for the full resource lifecycle.
- Add acceptance tests against a running API (can use a local Docker container or the VM).

### Phase 3: Remaining Resources

- Implement `cloudyhome_nfs_export`.
- Implement `cloudyhome_samba_share`.
- Implement `cloudyhome_iscsi_target`.
- Implement `cloudyhome_firewall_rule`.
- Add acceptance tests for each.

### Phase 4: Data Sources

- Implement `data.cloudyhome_dataset`.
- Implement `data.cloudyhome_zpool`.

### Phase 5: Integration Testing and Documentation

- End-to-end test: boot chain → API start → `terraform apply` → verify services and `services.yml`.
- Validate disaster recovery path: destroy VM state, reimport from API, apply.
- Document provider configuration, example Terraform modules, and import procedures.

## Open Questions

### Q1: TLS Cert in Terraform Provider Config

Whichever TLS strategy is chosen for the API (MVP2 Q2), the Terraform provider must be configured to trust or accept the cert. Options:

- `tls_cert_pem = file("nas-api.crt")` — pinned cert PEM, loaded from a local file.
- `insecure_skip_verify = true` — skip verification (dev/home-use only).
- A CA-signed cert (requires a private CA or Let's Encrypt, which adds infra complexity).

This is downstream of MVP2 Q2 but needs a decision for the provider schema design.

---

### Q2: Provider Repo Location

Should `terraform-provider-cloudyhome` live in:
- **This repo** (monorepo, Go code alongside Python) — simpler to develop and test together.
- **A separate repo** — standard Terraform provider convention, cleaner module boundaries, easier to publish to a registry later.

Monorepo is fine for MVP3. A separate repo can be split out later if needed.

---

### Q3: Disaster Recovery — Import vs. Auto-Detect

Should `terraform import` be mandatory to seed state after a disaster recovery rebuild, or should the provider silently handle the case where a resource already exists when POST is attempted (detect from the API response and switch to a GET/PUT instead)?

- **Import required:** Explicit, safe, standard Terraform workflow. Operator runs `terraform import` for each resource before `terraform apply`. Risk: operator forgets and `terraform apply` tries to re-create, API returns an error or recreates on top.
- **Auto-detect:** Provider attempts POST; if the resource exists (API returns a conflict or the resource is found via GET), it reads current state and diffs. Cleaner UX but non-standard Terraform behavior.
