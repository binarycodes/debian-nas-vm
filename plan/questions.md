# Open Questions

## MVP3 Questions

### Q1: Non-deletable singleton lifecycle in Terraform

MVP2 has singletons that only support GET/PUT (no POST/DELETE): `cloudyhome_host`, `cloudyhome_general`, `cloudyhome_samba_global`, `cloudyhome_iscsi_global`. These always exist after bootstrap and can't be created or destroyed.

Terraform's CRUD model assumes create/delete. Options:

- **A)** Make them data sources only, not resources. Read-only in Terraform, operator manages via API directly.
- **B)** Terraform `create` = import existing + PUT. Terraform `delete` = no-op (resource stays, just removed from Terraform state). Document this behavior.
- **C)** Add POST/DELETE to these MVP2 endpoints so the Terraform lifecycle maps cleanly.

**Impact:** Affects both MVP2 (API design) and MVP3 (provider resource model).

---

### Q2: Password drift detection

Samba and FTP user passwords are sensitive. The API likely shouldn't return plaintext passwords in GET responses. But if GET doesn't return the password, Terraform can't detect drift on password fields.

Options:

- **A)** API returns passwords in GET (plaintext over TLS). Terraform can detect drift. Simple but passwords are in Terraform state.
- **B)** API never returns passwords in GET. Password field is marked write-only in Terraform (plan modifier ignores server state for that field). Terraform can set passwords but can't detect if they changed out-of-band.
- **C)** API returns a hash/checksum of the password in GET. Terraform compares checksums for drift detection without storing the plaintext.

**Impact:** Affects both MVP2 (GET response shape) and MVP3 (schema design).

---

### Q3: Provider connectivity check — health vs ready

The provider uses `GET /v1/health` as a connectivity check. But `/v1/health` only confirms the process is running — it doesn't confirm the API can process mutations (SQLite loaded, etc.). `/v1/ready` does.

Should the provider use `/v1/ready` instead of `/v1/health` for the connectivity check? Or check both — `/v1/health` for "can I reach the API" and `/v1/ready` before any mutation?

---

### Q4: CONFLICT handling on create — standard vs auto-detect

The plan says: on POST 409 CONFLICT, the provider reads the existing resource and switches to PUT. This is convenient (especially for disaster recovery) but non-standard Terraform behavior. Standard Terraform would fail on conflict and require explicit `terraform import`.

Options:

- **A)** Auto-detect (current plan): POST → 409 → GET → PUT. Convenient, handles DR naturally.
- **B)** Standard: POST → 409 → fail with error "resource already exists, use terraform import". Explicit, predictable.
- **C)** Configurable: provider-level flag `auto_adopt_existing = true/false`.

---

### Q5: Import ID format for singletons

Collection resources use the key/name as the import ID (e.g., `terraform import cloudyhome_dataset.media shares_media`). For singletons like `cloudyhome_garage` or `cloudyhome_host`, there's no natural ID since only one instance exists.

Options:

- **A)** Use a fixed sentinel like `terraform import cloudyhome_garage.main singleton` or just `terraform import cloudyhome_garage.main ""`.
- **B)** Import ID is not required — the provider knows to GET the singleton directly.

---

### Q6: Bootstrap import ordering

When importing bootstrap resources into Terraform state, the operator must import in dependency order (datasets before exports/shares, users before shares). Should the provider documentation include a recommended import order, or should a helper script/command be provided?

---

### Q7: TLS cert in Terraform provider config

The API generates a self-signed cert at first boot (unless a cert is injected via Packer/cloud-init). The Terraform provider must be configured to trust it. Options:

- `tls_cert_pem = file("nas-api.crt")` — pinned cert PEM, loaded from a local file.
- `insecure_skip_verify = true` — skip verification (dev/home-use only).
- Inject a stable cert via Packer so it's known at Terraform config time.

---

### Q8: Provider repo location

Should `terraform-provider-cloudyhome` live in:

- **This repo** (monorepo, Go code alongside Python) — simpler to develop and test together.
- **A separate repo** — standard Terraform provider convention, cleaner module boundaries, easier to publish to a registry later.

Monorepo is fine for MVP3. A separate repo can be split out later if needed.
