# Open Questions

---

## MVP2 Questions

### Q1: API Port

What port should the management API listen on?

The firewall section of `services.yml` requires this port to be explicitly allowed from admin hosts. It must not conflict with NFS (2049), Samba (445, 139), iSCSI (3260), Garage (3900, 3901, 3902), or FTP (21, passive range).

**Candidates:** 9090, 8443, 7443, 19090

**Impact:** Baked into example `services.yml`, packer-checklist.md, and eventually the Terraform provider default config in MVP3.

---

### Q2: TLS Certificate Strategy

The API must serve HTTPS. Three options:

**Option A — Self-signed cert generated at first boot**
- A one-shot systemd service or API startup hook runs `openssl req -x509` and writes to `/etc/cloudyhome/api/tls.{crt,key}`.
- Clients (curl, web UI, Terraform provider) must trust or pin this cert explicitly.
- Pro: fully automated, no external dependencies.
- Con: cert changes if the VM is rebuilt; clients must be updated with the new cert each time.

**Option B — Cert injected at Packer build time**
- A TLS cert/key pair is generated externally and injected alongside `services.yml` and `secrets.enc.yaml`.
- Survives VM rebuilds if the same cert/key pair is re-injected.
- Pro: stable cert across rebuilds.
- Con: adds an extra Packer provisioner step and a cert management concern.

**Option C — `insecure_skip_verify` for now, revisit later**
- API serves with a self-signed cert but clients skip verification.
- Simplest for development. Acceptable for a home NAS on a private RFC1918 network.
- Pro: no cert pinning complexity for any client.
- Con: MITM possible on the LAN (low risk for home use).

Which strategy is acceptable?

---

### Q3: Dataset Path Derivation in the API

In MVP1, `StorageDataset` has both a `key` (dict key in YAML) and a `path` (explicit full path like `/zpool0/media`). The API could handle this two ways:

**Option A — Caller provides path explicitly**
- POST body includes `{ "key": "media", "path": "/zpool0/media", "quota": "500G" }`.
- Matches the existing model exactly. No inference magic.
- Con: redundant; caller must know the pool name and provide a consistent path.

**Option B — API derives path from pool name + key**
- POST body is `{ "key": "media", "quota": "500G" }`.
- API derives `path = /<pool>/<key>` automatically.
- Pro: simpler surface, fewer validation errors.
- Con: less explicit; non-default mountpoints are not possible.

Which is preferred?

---

### Q4: services.yml Persistence on the Zpool

After a VM rebuild, the Packer image has the original bootstrap `services.yml`. API clients (Terraform in MVP3, or manual calls) must re-converge the running state.

Should `services.yml` also be stored on a ZFS dataset (on the zpool, which survives VM rebuilds) so that after a rebuild and pool import the API finds the latest config automatically?

- **Yes (zpool copy):** The API writes `services.yml` to both `/var/lib/cloudyhome/nas/services.yml` and a mirrored path on the zpool (e.g., `/zpool0/.cloudyhome/services.yml`). On startup, if the zpool mirror exists, it takes precedence over the baked-in copy.
- **No (re-convergence only):** The baked-in `services.yml` is the recovery starting point. Clients re-converge afterward. Simpler, no dual-write logic.

---

### Q5: iSCSI zvol Destroy Behavior

When a `DELETE /v1/iscsi/targets/{name}` request is processed:
- Should the API automatically `zfs destroy` the backing zvol(s)?
- Or should zvol cleanup require a separate `DELETE /v1/datasets/{key}` call that the operator sequences explicitly?

Automatically destroying the zvol is convenient but irreversible. Requiring an explicit dataset delete is safer but more steps.

**Leaning toward:** require explicit dataset delete (consistent with how NFS/Samba also require explicit dataset management). Confirm?

---

### Q6: Adding New Secrets at Runtime

When an operator wants to add a new NFS export with a new client CIDR (which must be a `cidr_ref` pointing to a secret in `secrets.enc.yaml`), they must:
1. Update `secrets.enc.yaml` with the new value.
2. Re-encrypt and re-deploy it (SSH + file replace, or Packer rebuild).
3. Then call the API.

MVP2 does not support updating `secrets.enc.yaml` at runtime. Is this acceptable, or do we need a secrets-update API endpoint that accepts a new encrypted `secrets.enc.yaml` and triggers an in-process SOPS re-decrypt?

---

### Q7: Pinned Management API Firewall Rule

The firewall rule that allows access to the management API port must always be present — if it is deleted, the API becomes unreachable. Three options:

**Option A — Warn at startup:** On startup, check that a rule for the API port exists in `services.yml`. Log a warning if missing, but don't fail.

**Option B — Pin in nftables template:** `nftables.conf.j2` always emits an allow rule for the API port regardless of the `firewall.rules` list. Cannot be accidentally deleted.

**Option C — Operator's responsibility:** Document the requirement. No enforcement.

Option B is the safest but departs from the fully declarative firewall model. Option A is a soft guard. Option C is simplest.

---

### Q8: Samba User Deletion

When the last Samba share referencing an OS user is deleted via the API, should the OS user be automatically deleted?

Pros of auto-delete: clean system state.
Cons: the user may own files on a dataset; `userdel` could leave orphaned UIDs on those files. If two shares reference the same user and one is deleted, the user must obviously be retained.

**Leaning toward:** never auto-delete OS users in MVP2. Document it as a manual cleanup step. Confirm?

