# NAS Migration Architecture Plan

## 1. Problem Statement

Current state:
- The NAS currently runs TrueNAS SCALE.
- Goal is to migrate to a platform with full Infrastructure-as-Code (IaC) control and an immutable/ephemeral OS workflow.

Constraints and requirements:
- Hardware is dedicated to NAS-only usage.
- Virtualization option selected: Proxmox host running a single Debian NAS VM.
- Storage is 6 motherboard SATA disks (no HBA), passed into the VM by stable disk IDs.
- Existing ZFS pool already exists (`zpool0`) and must be imported at boot.
- Services to configure on boot:
  - NFS
  - Samba
  - iSCSI
  - S3 (Garage)
  - FTP (pure FTP for scanner uploads; no SFTP)
- Declarative service intent should be in YAML template(s) baked into image under `/var/lib/cloudyhome/nas/...`.
- Secrets are in a baked SOPS+AGE encrypted file; cloud-init provides the AGE private key via `write_files`.
- Generated runtime configs should live in `/etc` (not persisted in pool by default).

## 2. Objectives

- Achieve reproducible NAS provisioning through image + boot-time generation.
- Minimize host drift by treating VM OS as replaceable.
- Keep sensitive material out of plaintext image and out of long-lived cloud-init artifacts.
- Ensure boot process is deterministic, idempotent, and safe to rerun.

## 3. Why Proxmox + Single Debian VM

Rationale:
- Preserves IaC control over VM lifecycle while keeping NAS role isolated.
- Supports image-based workflows (Packer golden image + re-deploy).
- Allows rollback/snapshot and controlled upgrade paths.
- Maintains single-purpose operational simplicity (one VM, one role).

Tradeoff accepted:
- Slightly more stack complexity than Debian bare metal, but much better lifecycle and rebuild ergonomics.

## 4. High-Level Design

### 4.1 Platform Layout
- Proxmox host:
  - Minimal configuration, stable updates.
  - VM disk passthrough by `/dev/disk/by-id/*` for all 6 SATA disks.
- Debian NAS VM:
  - Includes ZFS, NFS, Samba, iSCSI tooling, Podman, cloud-init, sops, age.
  - Includes bootstrap scripts and systemd units.

### 4.2 Configuration Inputs
- Non-secret declarative template (baked in image):
  - `/var/lib/cloudyhome/nas/services.yml`
- Secret encrypted file (baked in image):
  - `/var/lib/cloudyhome/nas/secrets.enc.yaml`
- AGE private key (cloud-init injected):
  - Delivered via cloud-init `write_files` to `/etc/sops/age/keys.txt` at first boot.
  - SOPS uses this path automatically as the AGE identity.
  - The image contains encrypted secrets but never the decryption key.

### 4.3 Generated Runtime Outputs
- `/etc/exports.d/cloudyhome.exports` (NFS)
- `/etc/samba/smb.conf` (Samba)
- `/etc/target/saveconfig.json` (iSCSI)
- `/etc/cloudyhome/garage.toml` (Garage container config input)
- `/etc/cloudyhome/ftp.env` (FTP container environment)
- `/etc/containers/systemd/cloudyhome-garage.container` (root Quadlet)
- `/etc/containers/systemd/cloudyhome-ftp.container` (root Quadlet)

All outputs are generated atomically from template + decrypted secrets at boot.

## 5. Boot Workflow

1. `cloud-init` finishes; AGE private key is present at `/etc/sops/age/keys.txt` (written via `write_files`).
2. `zfs-import-existing.service`:
   - Checks whether `zpool0` is imported.
   - Imports `zpool0` if needed.
3. `zfs-mount.service` mounts datasets.
4. `nas-render-config.service`:
   - Decrypts secrets from baked SOPS file using AGE key at `/etc/sops/age/keys.txt`.
   - Merges secret + non-secret data.
   - Renders NFS/Samba/iSCSI/Garage/FTP configs into `/etc`.
   - Validates generated config syntax.
   - Cleans decrypted material from `/run`.
5. `nas-apply-config.service`:
   - Creates missing datasets and zvols.
   - Provisions Samba users into `tdbsam`.
   - Applies/loads iSCSI config.
   - Starts/reloads NFS, Samba, iSCSI, Garage, and FTP services.
6. `garage-bootstrap.service`:
   - Runs after `cloudyhome-garage.service` is up.
   - Checks Garage layout via admin API; assigns and applies layout if not yet configured.

## 6. Systemd Design

## 6.1 `zfs-import-existing.service`
- Type: `oneshot`
- Purpose: import existing `zpool0` if not already imported.
- Order: before `zfs-mount.service`.
- If the pool is already imported: no-op.
- If the pool is found and importable: import it.
- If the pool is not found: exit cleanly. Pool creation is a manual, out-of-band operation — never attempted here. Downstream services that require ZFS paths will fail naturally if the pool is absent.

## 6.2 `nas-render-config.service`
- Type: `oneshot`
- `After=cloud-init.target zfs-mount.service`
- Runs `/usr/local/sbin/nas-render-config`.

## 6.3 `nas-apply-config.service`
- Type: `oneshot`
- `After=nas-render-config.service`
- Performs apply/reload behavior via explicit `systemctl start` and `systemctl reload-or-restart` calls — not via `Wants=` or `Before=` dependencies.
- Service interaction order:
  1. Create missing datasets and zvols.
  2. Provision Samba users into `tdbsam`.
  3. `systemctl reload-or-restart nfs-server.service`
  4. `systemctl reload-or-restart smbd.service`
  5. `systemctl restart target.service` (iSCSI — full restart to apply new saveconfig.json)
  6. `systemctl start cloudyhome-garage.service`
  7. `systemctl start cloudyhome-ftp.service`
- Decrypts `/var/lib/cloudyhome/nas/secrets.enc.yaml` to a `mktemp`-created file under `/run` using the AGE key at `/etc/sops/age/keys.txt`. Removes the temp file on exit (trap on EXIT).
- Provisions Samba users into `tdbsam` via `smbpasswd`/`pdbedit`. Runs before `smbd.service` starts.

## 6.4 `garage-bootstrap.service`
- Type: `oneshot`
- `After=cloudyhome-garage.service`
- `Wants=cloudyhome-garage.service`
- Purpose: idempotent Garage layout assignment.
- Decrypts `/var/lib/cloudyhome/nas/secrets.enc.yaml` to a `mktemp`-created file under `/run` using the AGE key at `/etc/sops/age/keys.txt`. Removes the temp file on exit (trap on EXIT).
- All `garage` CLI calls are executed via `podman exec cloudyhome-garage garage ...` — no host-side Garage binary required.
- Obtains the local node ID via `podman exec cloudyhome-garage garage node id`.
- Queries the Garage admin API (`GET /v1/layout`); if the current layout has no roles assigned, runs:
  1. `podman exec cloudyhome-garage garage layout assign -z garage -c <capacity> <node-id>`
  2. `podman exec cloudyhome-garage garage layout apply --version 1`
- If roles are already present in the API response, exits successfully without making changes.
- `capacity` is derived from `services.yml` (`garage.layout_capacity`).
- Single-node deployment: only one node ID is expected.

## 7. Script Design (`nas-render-config`)

Core behavior:
- Acquire lock (`flock /run/nas/render.lock`) to avoid concurrent runs.
- Decrypt `/var/lib/cloudyhome/nas/secrets.enc.yaml` to a `mktemp`-created file under `/run` using SOPS with AGE key at `/etc/sops/age/keys.txt`. Remove the temp file on exit (trap on EXIT).
- Merge `/var/lib/cloudyhome/nas/services.yml` + decrypted secrets.
- Render target files into `/etc` with temp-file + atomic move.
- Set permissions:
  - restrictive mode for files containing credentials.
- Validate:
  - `testparm -s` (Samba) — only runtime validation needed; all other outputs are generated from Pydantic-validated input and trusted programmatically.
- Remove decrypted intermediates from `/run`.
- Exit non-zero on any failed validation.

Idempotency (hard requirement):
- Every render and apply step must be safe to run on every boot, including reboots with no config change.
- Detect file changes before restart/reload; do not restart services unnecessarily.
- Do not rewrite unchanged configs unnecessarily.
- Samba user provisioning, iSCSI restore, dataset creation, and service reloads must all handle already-current state gracefully.
- Any step that fails idempotency is a bug.

## 8. Data Model (Template Contract)

`services.yml` defines non-secret structure:
- NFS export paths/options
- Samba global + shares
- iSCSI target/LUN mapping
- Garage network and non-secret parameters
- FTP listeners, passive port range, and upload policy

`secrets.enc.yaml` defines sensitive values:
- Samba users/passwords
- iSCSI CHAP credentials
- Garage RPC/S3/admin secrets/keys
- FTP local/virtual account credentials

Renderer contract:
- Strict schema validation before writing `/etc`.
- Fail closed on missing required secret fields.

## 9. Security Model

Controls:
- AGE private key delivered via cloud-init `write_files` — never in process args, environment, or shell history.
- Any service that needs secrets decrypts independently: SOPS → `mktemp` under `/run` (tmpfs) → use → `trap on EXIT` cleanup. No service depends on another's decryption output.
- Decrypted temp files exist only for the lifetime of the process that created them; removed unconditionally on exit.
- Restrictive ownership and modes on generated `/etc` files.
- Journald/logging avoids printing secret values.

Risk notes:
- `/etc/sops/age/keys.txt` persists on the VM filesystem for the lifetime of the VM; access must be restricted to root (`0600`).
- Cloud-init `write_files` should set `permissions: '0600'` and `owner: root:root` explicitly.
- `discovery_auth: "none"` on iSCSI means any host that can reach TCP 3260 can enumerate target IQNs. Mitigated by restricting port 3260 at the firewall to known initiator subnets only. Session CHAP protects actual data access regardless.

## 10. Firewall Port Reference

All ports are on the NAS VM. Restrict source IPs at the firewall to the minimum required subnet.

| Port(s)       | Protocol | Service         | Source restriction              | Notes                                               |
|---------------|----------|-----------------|---------------------------------|-----------------------------------------------------|
| 22            | TCP      | SSH             | Admin hosts only                | VM management access                                |
| 111           | TCP+UDP  | rpcbind         | NFS client subnet               | Required for NFSv3; not needed if NFSv4-only        |
| 2049          | TCP+UDP  | NFS             | NFS client subnet               | Main NFS port; NFSv4 only needs TCP 2049            |
| 139           | TCP      | Samba (NetBIOS) | LAN subnet                      | Legacy NetBIOS session; not needed for SMB2/3 only  |
| 445           | TCP      | Samba (SMB)     | LAN subnet                      | Primary Samba port for SMB2/3                       |
| 3260          | TCP      | iSCSI           | Initiator subnet only           | Both discovery and session traffic; restrict tightly to prevent target enumeration by untrusted hosts |
| 3900          | TCP      | Garage S3       | S3 client subnet                | Garage S3-compatible object storage API             |
| 3901          | TCP      | Garage RPC      | Loopback only                   | Single-node: no inter-node RPC needed; bind to loopback |
| 3903          | TCP      | Garage admin    | Loopback only                   | Admin API; never exposed externally                 |
| 21            | TCP      | FTP control     | Scanner IP only                 | Pure FTP control channel for scanner uploads        |
| 21000–21010   | TCP      | FTP passive     | Scanner IP only                 | Passive data channels; range defined in services.yml |

## 11. Operations and Lifecycle

Build/deploy model:
1. Build Debian image with Packer (existing, out of scope).
2. Provision/update VM with Terraform (existing, out of scope).
3. On first boot, cloud-init injects AGE key via `write_files`.
4. systemd imports pool, renders configs, starts services.

Deliverable scope (this project):
- Python renderer script (`/usr/local/sbin/nas-render-config`)
- systemd units: `zfs-import-existing.service`, `nas-render-config.service`, `nas-apply-config.service`, `garage-bootstrap.service`
- Jinja2 templates for all generated configs (NFS exports, smb.conf, iSCSI saveconfig.json, garage.toml, ftp.env, Quadlet units)
- `services.yml` canonical example (baked into image by Packer)
- `secrets.enc.yaml` schema example (encrypted and baked into image by Packer)
- All files are placed under a source tree that Packer copies into the image

Recovery drill target:
- Recreate VM from image + IaC.
- Reattach same disks by ID.
- Import `zpool0`.
- Regenerate configs.
- Validate client access for NFS/Samba/iSCSI/S3/FTP.

## 12. Implementation Plan

1. Create image contents:
   - Install packages and binaries.
   - Add `/var/lib/cloudyhome/nas/services.yml`.
   - Add `/var/lib/cloudyhome/nas/secrets.enc.yaml`.
   - Add renderer/apply scripts and systemd units.
2. Implement systemd units and ordering.
3. Implement schema + render logic for NFS and Samba first.
4. Add iSCSI and Garage generation.
5. Add FTP generation.
6. Add validation and idempotent restart policy.
7. Test:
   - clean boot
   - repeated boot
   - missing AGE key (`/etc/sops/age/keys.txt` absent)
   - invalid template
   - invalid secrets
   - service-specific syntax errors
8. Perform full rebuild/recovery rehearsal.

## 13. Open Decisions

### 13.1 Decided Constraints
- **No credential persistence to ZFS.** All credentials are reprovisioned from secrets on every boot (including reboots). Config changes mean VM replacement, so secrets in the image are always current. All apply steps must be idempotent and safe to rerun.

- Container runtime is fixed: root Podman Quadlets for all containers.
- Render/config generation language: **Python**. Strict schema validation via `pydantic`; no config is written unless all validation passes. Libraries: `pyyaml`, `pydantic`, `jinja2`, `tomli-w`.
- iSCSI backend: **direct JSON generation**. The renderer builds `/etc/target/saveconfig.json` from `services.yml` + secrets. `target.service` (rtslib-fb) restores from it on boot. No `targetcli` interactive session involved.
- **NFS and Samba run as host services** (`nfs-kernel-server`, `smbd`), not containers. NFS is a kernel subsystem; containerizing it provides no isolation benefit and adds significant complexity. Samba follows the same decision for consistency. The VM is the isolation boundary. Garage and FTP remain containerized as Podman Quadlets.

### 13.2 Dataset and zvol Creation
During `nas-apply-config.service`:
- **Datasets**: created if missing using `zfs create -p <path>`. Parent datasets created automatically. Existing datasets left untouched. All dataset paths use the mount path form (e.g. `/zpool0/shares/media`).
- **zvols** (iSCSI LUNs): created if missing using `zfs create -V <size> <path>`. Existing zvols left untouched (size is not modified). Path uses dataset name form (e.g. `zpool0/iscsi/vmstore`); block device is derived as `/dev/zvol/<path>`.

## 14. Finalized `services.yml` Structure

This section defines the canonical non-secret schema to be baked into:
- `/var/lib/cloudyhome/nas/services.yml`

All secret values referenced here are resolved from decrypted `secrets.enc.yaml` during render.

### 14.1 Design Principles
- `services.yml` contains only non-secret intent and topology.
- Secrets are referenced by stable IDs/paths and resolved at render time.
- Paths that point to storage data must be under `zpool0` mount hierarchy.
- Schema is explicit and strict; unknown top-level keys should fail validation.

### 14.2 Top-Level Keys
- `version` (required, integer): schema version. Initial value: `1`.
- `storage` (required, map): pool and dataset conventions.
- `nfs` (optional, map): NFS export definitions.
- `samba` (optional, map): Samba global and share definitions.
- `iscsi` (optional, map): iSCSI IQN and LUN mapping.
- `garage` (optional, map): Garage process and S3 endpoint settings.
- `ftp` (optional, map): Pure FTP daemon settings for scanner uploads.

At least one of `nfs`, `samba`, `iscsi`, `garage`, or `ftp` must be present.

### 14.3 Canonical Schema (Field Contract)

```yaml
version: 1

storage:
  pool: "zpool0"
  datasets:                            # canonical inventory for validation; can include currently unused datasets
    - "/zpool0/system"                 # NAS system/state datasets
    - "/zpool0/shares"                 # file shares parent dataset
    - "/zpool0/iscsi"                  # zvol parent dataset for iSCSI LUNs
    - "/zpool0/backups"                # backup target dataset (snapshots/replication landing)

nfs:
  exports:
    - name: "media"
      path: "/zpool0/shares/media"
      clients:
        - cidr: "10.0.0.0/24"
          options: ["rw", "sync", "no_subtree_check"]
          identity_map:
            mode: "root_squash"      # one of: none|root_squash|all_squash|no_root_squash
            anon_uid: null           # required when mode=all_squash
            anon_gid: null           # required when mode=all_squash
      options: []                    # optional export-level options appended to all clients
      enabled: true                  # optional, default true

samba:
  global:
    workgroup: "WORKGROUP"
    server_string: "CloudyHome NAS" # optional
    map_to_guest: "Bad User"         # optional
  shares:
    - name: "media"
      path: "/zpool0/shares/media"
      browsable: true
      read_only: false
      guest_ok: false
      users_ref: ["alice"]                  # usernames from samba.users[] in secrets; maps to valid_users in smb.conf
      write_list: []                        # optional
      force_user: ""                        # optional
      force_group: ""                       # optional
      create_mask: "0660"                   # optional
      directory_mask: "0770"                # optional
      enabled: true                         # optional, default true

iscsi:
  base_iqn: "iqn.2026-03.home.arpa:nas01"
  portals:
    - "10.0.0.10:3260"
  targets:
    - name: "vmstore"
      iqn_suffix: "vmstore"
      luns:
        - lun: 0
          type: "zvol"
          path: "zpool0/iscsi/vmstore"   # zvol dataset name; block device derived as /dev/zvol/<path>
          size: "100G"                   # zvol size; created if not present, left untouched if exists
          readonly: false
      auth:
        discovery_auth: "none"           # one of none|chap
        session_auth: "chap"             # one of none|chap
        chap_secret_ref: "iscsi/vmstore" # key in secrets file
      initiators:
        - "iqn.1993-08.org.debian:client1"  # empty list means no initiator is allowed (deny all)
      enabled: true

garage:
  enabled: true
  runtime: "podman-quadlet-root"
  quadlet_name: "cloudyhome-garage"
  image: "dxflrs/garage:latest"
  rpc_bind: "10.0.0.10:3901"
  s3_bind: "10.0.0.10:3900"
  admin_bind: "127.0.0.1:3903"         # admin API; loopback-only by default
  s3_region: "garage"
  replication_mode: "none"              # single-node default
  data_dir: "/zpool0/system/garage/data"
  metadata_dir: "/zpool0/system/garage/meta"
  layout_capacity: "1G"                 # capacity string passed to `garage layout assign`
  admin_token_ref: "garage/admin_token" # key in secrets file
  rpc_secret_ref: "garage/rpc_secret"   # key in secrets file

ftp:
  enabled: true
  runtime: "podman-quadlet-root"
  quadlet_name: "cloudyhome-ftp"
  image: "delfer/alpine-ftp-server:latest"
  bind_address: "10.0.0.10"                 # maps to ADDRESS env
  control_port: 21
  passive_ports:
    min: 21000                              # maps to MIN_PORT env
    max: 21010                              # maps to MAX_PORT env
  users_ref: "ftp/users"                    # maps to USERS env
  upload_root: "/zpool0/shares/scanner-inbox"
  tls:
    enabled: false
    cert_path: ""                           # optional, container path
    key_path: ""                            # optional, container path
```

### 14.4 Validation Rules
- `version` must equal `1`.
- `storage.pool` must be `zpool0` for this deployment.
- `storage.datasets` must be a non-empty list of unique dataset paths.
- Each dataset entry must start with `/zpool0/` (mount path form).
- Any `path` intended for data export must start with `/zpool0/`.
- `nfs.exports[*].clients` must be non-empty when export is enabled.
- `nfs.exports[*].clients[*].cidr` is required.
- `nfs.exports[*].clients[*].options` is optional; defaults to `[]`.
- `nfs.exports[*].clients[*].identity_map.mode` is optional; defaults to `root_squash`.
- `nfs.exports[*].clients[*].identity_map.mode` must be one of:
  - `none`
  - `root_squash`
  - `all_squash`
  - `no_root_squash`
- If `identity_map.mode=all_squash`, both `anon_uid` and `anon_gid` are required.
- `samba.shares[*].name` must be unique.
- `samba.shares[*].users_ref` must be a non-empty list of usernames. Every entry must exist in `samba.users[*].username` in secrets. Unresolved usernames fail closed.
- `iscsi.targets[*].name` must be unique.
- `iscsi.targets[*].luns[*].lun` must be unique per target.
- `iscsi.targets[*].initiators` defaults to deny-all when empty. The renderer must not allow implicit open access — an empty list is valid and means no initiator is permitted.
- `iscsi.targets[*].luns[*].size` is required for `type=zvol` and must be a valid ZFS size string (e.g. `"100G"`).
- `iscsi.targets[*].luns[*].path` uses dataset name form (no leading slash); block device path is derived as `/dev/zvol/<path>`.
- `garage.enabled=true` requires:
  - `runtime=podman-quadlet-root`
  - non-empty `quadlet_name`
  - non-empty `image` (Garage container image is `dxflrs/garage`; version pinning is managed in the Quadlet file, not validated here)
  - both `admin_token_ref` and `rpc_secret_ref`
  - `admin_bind` must be specified; defaults to `127.0.0.1:3903` if omitted (loopback-only)
- `ftp.enabled=true` requires:
  - `runtime=podman-quadlet-root`
  - non-empty `quadlet_name`
  - non-empty `image` (FTP container image is `delfer/alpine-ftp-server`; version pinning is managed in the Quadlet file, not validated here)
  - `control_port=21` unless explicitly overridden
  - valid passive range (`passive_ports.min <= passive_ports.max`)
  - `upload_root` under `/zpool0/`
  - `users_ref` present and resolvable in secrets
- `ftp.tls.enabled=true` requires both `tls.cert_path` and `tls.key_path`.

### 14.5 Secrets Mapping Contract

`secrets.enc.yaml` is keyed by reference path used in `services.yml`:

```yaml
iscsi:
  vmstore:
    chap_user: "vmstore-user"
    chap_password: "REDACTED"
garage:
  admin_token: "REDACTED"
  rpc_secret: "REDACTED"
samba:
  users:
    - username: "alice"
      password: "REDACTED"
ftp:
  users:
    - username: "scanner1"
      password: "REDACTED"
```

Note: `delfer/alpine-ftp-server` accepts `user|pass|uid|gid|homedir` but all fields after `password` are optional. This deployment uses only `username` and `password`. The renderer constructs the `USERS` env var as `user1|pass1:user2|pass2:...`.

Rules:
- References are resolved as slash-delimited paths (example: `garage/admin_token`).
- Renderer must fail if a referenced key is absent.
- Renderer must not print resolved secret values in logs.

Samba user mapping:
- Each share's `users_ref` is a list of usernames. The renderer validates each against `samba.users[*].username` in secrets and renders them as `valid_users` in `smb.conf`.
- Different shares may list different subsets of users for per-share access control.
- User provisioning (writing to `tdbsam`) is handled in `nas-apply-config.service`.
