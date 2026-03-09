# Open Questions

---

## MVP2 Questions

### Q1: API Port — DECIDED

**Decision:** Port **9443**. The API is always HTTPS.

The firewall section of `services.yml` requires this port to be explicitly allowed from admin hosts. It must not conflict with NFS (2049), Samba (445, 139), iSCSI (3260), Garage (3900, 3901, 3902), or FTP (21, passive range).

**Impact:** Baked into example `services.yml`, packer-checklist.md, and eventually the Terraform provider default config in MVP3.

---

### Q2: TLS Certificate Strategy — DECIDED

**Decision:** Generate a self-signed cert at first boot, but only if no cert is already present at the expected path. If a cert/key pair has been injected (by Packer, cloud-init, or any other mechanism) before first boot, it is used as-is.

**Behaviour:**
- A one-shot systemd service (`cloudyhome-nas-tls.service`) runs before `cloudyhome-nas-api.service`.
- It checks for `/etc/cloudyhome/api/tls.crt` and `/etc/cloudyhome/api/tls.key`.
- If both exist, it exits immediately (no-op).
- If either is missing, it generates a self-signed cert with `openssl req -x509` and writes both files.

**Implication for clients:** Clients that need a stable cert (Terraform provider, web UI) can inject one via Packer/cloud-init at a path the service checks first. Operators who don't care (e.g., home lab curl usage) get an auto-generated cert with `--insecure` or manual trust-on-first-use.

---

### Q3: Dataset Path Derivation in the API — DECIDED

**Decision:** Option A — caller provides path explicitly.

POST body includes `{ "key": "media", "path": "/zpool0/media", "quota": "500G" }`. Matches the existing model exactly. With a pool array, the API has no way to know which pool the caller intends, so path derivation is not possible.

---

### Q4: services.yml Persistence on the Zpool — DECIDED

**Decision:** No zpool mirroring. Keep it simple and automation-friendly.

The API exposes a download endpoint for the current `services.yml` and `secrets.enc.yaml`. An automation pipeline (CI, Terraform, cron) pulls the config after every change and feeds it into the next Packer build automatically. On rebuild, the baked-in copy is the starting point — no dual-write logic, no zpool dependency at startup. The burden of rebaking is on the automation, not the operator.

---

### Q5: iSCSI zvol Destroy Behavior — DECIDED

**Decision:** Explicit dataset delete required. Deleting an iSCSI target does not destroy the backing zvol — a separate `DELETE /v1/datasets/{key}` call is needed.

Keeps operations explicit and automation-friendly: pipelines sequence the calls deliberately, nothing is destroyed implicitly. Consistent with how NFS/Samba handle dataset lifecycle.

---

### Q6: Adding New Secrets at Runtime

When an operator wants to add a new NFS export with a new client CIDR (which must be a `cidr_ref` pointing to a secret in `secrets.enc.yaml`), they must:
1. Update `secrets.enc.yaml` with the new value.
2. Re-encrypt and re-deploy it (SSH + file replace, or Packer rebuild).
3. Then call the API.

MVP2 does not support updating `secrets.enc.yaml` at runtime. Is this acceptable, or do we need a secrets-update API endpoint that accepts a new encrypted `secrets.enc.yaml` and triggers an in-process SOPS re-decrypt?

---

### Q7: Pinned Management API Firewall Rule — DECIDED

**Decision:** Option C — operator's responsibility. Document it, no enforcement.

Operators may choose not to enable or use the API at all. Pinning a firewall rule for it would be presumptuous. The declarative firewall model stays clean.

---

### Q8: Samba User Deletion — DECIDED

**Decision:** No auto-delete of OS users. When the last share referencing a user is removed, the user remains on the system.

Rationale: the user may own files on a dataset; `userdel` could orphan UIDs. Explicit is safer and consistent with the automation-first principle — let the operator sequence cleanup deliberately.

Cleanup is handled via the users API (see Q9).

---

### Q9: OS User Management API

Since Samba users are not auto-deleted, orphaned OS users will accumulate over time. The API needs endpoints to let operators manage them:

- `GET /v1/users` — list OS users created by the API (Samba users), including which shares (if any) still reference them.
- `DELETE /v1/users/{username}` — delete an OS user explicitly. Should the API refuse if the user still owns files on a managed dataset, or just warn?

**Open:** Should delete be blocked when shares still reference the user, or should that be a client-side concern? Should file ownership checks be in scope for MVP2?

