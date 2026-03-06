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
- Secrets are in a baked SOPS+AGE encrypted file; cloud-init provides only decrypt token/pass.
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
- Cloud-init runtime secret input:
  - Token/pass written to `/run/nas/bootstrap.token` with `0600`.

### 4.3 Generated Runtime Outputs
- `/etc/exports.d/cloudyhome.exports` (NFS)
- `/etc/samba/smb.conf` (Samba)
- `/etc/target/saveconfig.json` or equivalent restore input (iSCSI)
- `/etc/cloudyhome/garage.toml` (Garage container config input)
- `/etc/cloudyhome/ftp.env` (FTP container environment)
- `/etc/containers/systemd/cloudyhome-garage.container` (root Quadlet)
- `/etc/containers/systemd/cloudyhome-ftp.container` (root Quadlet)

All outputs are generated atomically from template + decrypted secrets at boot.

## 5. Boot Workflow

1. `cloud-init` finishes and writes decrypt token/pass to `/run/nas/bootstrap.token`.
2. `zfs-import-existing.service`:
   - Checks whether `zpool0` is imported.
   - Imports `zpool0` if needed.
3. `zfs-mount.service` mounts datasets.
4. `nas-render-config.service`:
   - Decrypts secrets from baked SOPS file using token/pass.
   - Merges secret + non-secret data.
   - Renders NFS/Samba/iSCSI/Garage/FTP configs into `/etc`.
   - Validates generated config syntax.
   - Cleans decrypted material from `/run`.
5. `nas-apply-config.service`:
   - Applies/loads iSCSI config.
   - Reloads/restarts only required services.

## 6. Systemd Design

## 6.1 `zfs-import-existing.service`
- Type: `oneshot`
- Purpose: import existing `zpool0` if absent.
- Order: before `zfs-mount.service`.

## 6.2 `nas-render-config.service`
- Type: `oneshot`
- `After=cloud-init.service network-online.target zfs-mount.service`
- `Wants=network-online.target`
- Runs `/usr/local/sbin/nas-render-config.sh`.

## 6.3 `nas-apply-config.service`
- Type: `oneshot`
- `After=nas-render-config.service`
- Performs validation/apply/reload behavior.
- Ensures service startup ordering for:
  - `nfs-server.service`
  - `smbd.service`
  - iSCSI target service (`target.service` or distro equivalent)
  - `cloudyhome-garage.service`
  - `cloudyhome-ftp.service`

## 7. Script Design (`nas-render-config.sh`)

Core behavior:
- Acquire lock (`flock`) to avoid concurrent runs.
- Ensure ZFS pool import (`zpool list zpool0 || zpool import zpool0`).
- Read token from file (`/run/nas/bootstrap.token`), never as command arg.
- Decrypt `/var/lib/cloudyhome/nas/secrets.enc.yaml` to `/run/nas/secrets.yaml`.
- Merge `/var/lib/cloudyhome/nas/services.yml` + decrypted secrets.
- Render target files into `/etc` with temp-file + atomic move.
- Set permissions:
  - restrictive mode for files containing credentials.
- Validate:
  - `exportfs -ra` (NFS)
  - `testparm -s` (Samba)
  - iSCSI parse/restore precheck
  - Garage config and Quadlet preflight
  - FTP container preflight (env + service unit validity)
- Remove decrypted intermediates and token.
- Exit non-zero on any failed validation.

Idempotency:
- Safe for every boot.
- Detect file changes before restart/reload.
- Do not rewrite unchanged configs unnecessarily.

## 8. Data Model (Template Contract)

`services.yml` defines non-secret structure:
- NAS identity
- NFS export paths/options
- Samba global + shares
- iSCSI target/LUN mapping
- Garage network and non-secret parameters
- FTP listeners, passive port range, and upload policy

`secrets.enc.yaml` defines sensitive values:
- Samba users/password hashes or auth material
- iSCSI CHAP credentials
- Garage RPC/S3/admin secrets/keys
- FTP local/virtual account credentials

Renderer contract:
- Strict schema validation before writing `/etc`.
- Fail closed on missing required secret fields.

## 9. Security Model

Controls:
- Decrypt token/pass is short-lived and only in `/run` (`tmpfs`).
- No token in process args, environment, or shell history.
- Decrypted secrets exist only transiently in `/run`; removed after render.
- Restrictive ownership and modes on generated `/etc` files.
- Journald/logging avoids printing secret values.

Risk notes:
- Cloud-init artifacts can retain data if not explicitly handled.
- Mitigation: clean token file post-use and disable verbose command echo in bootstrap.

## 10. Operations and Lifecycle

Build/deploy model:
1. Build Debian image with Packer.
2. Provision/update VM with Terraform (Proxmox provider).
3. On first boot, cloud-init injects token/pass and identity values.
4. systemd imports pool, renders configs, starts services.

Recovery drill target:
- Recreate VM from image + IaC.
- Reattach same disks by ID.
- Import `zpool0`.
- Regenerate configs.
- Validate client access for NFS/Samba/iSCSI/S3.
- Validate client access for NFS/Samba/iSCSI/S3/FTP.

## 11. Implementation Plan

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
   - missing token
   - invalid template
   - invalid secrets
   - service-specific syntax errors
8. Perform full rebuild/recovery rehearsal.

## 12. Open Decisions

- Final choice of template/render implementation language (shell+yq/jq, Python, or Go).
- iSCSI backend config format (`targetcli` save/restore method vs direct JSON generation).
- Whether to persist any derived credentials to ZFS dataset vs regenerate each boot.

### 12.1 Decided Constraints

- Container runtime is fixed: root Podman Quadlets for all containers.

## 13. Finalized `services.yml` Structure

This section defines the canonical non-secret schema to be baked into:
- `/var/lib/cloudyhome/nas/services.yml`

All secret values referenced here are resolved from decrypted `secrets.enc.yaml` during render.

### 13.1 Design Principles
- `services.yml` contains only non-secret intent and topology.
- Secrets are referenced by stable IDs/paths and resolved at render time.
- Paths that point to storage data must be under `zpool0` mount hierarchy.
- Schema is explicit and strict; unknown top-level keys should fail validation.

### 13.2 Top-Level Keys
- `version` (required, integer): schema version. Initial value: `1`.
- `identity` (required, map): node-level naming and network defaults.
- `storage` (required, map): pool and dataset conventions.
- `nfs` (optional, map): NFS export definitions.
- `samba` (optional, map): Samba global and share definitions.
- `iscsi` (optional, map): iSCSI IQN and LUN mapping.
- `garage` (optional, map): Garage process and S3 endpoint settings.
- `ftp` (optional, map): Pure FTP daemon settings for scanner uploads.

At least one of `nfs`, `samba`, `iscsi`, `garage`, or `ftp` must be present.

### 13.3 Canonical Schema (Field Contract)

```yaml
version: 1

storage:
  pool: "zpool0"
  datasets:                            # canonical inventory for validation; can include currently unused datasets
    - "/zpool0"                        # pool root mount
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
      valid_users: ["@nasusers"]     # optional
      write_list: []                 # optional
      force_user: ""                 # optional
      force_group: ""                # optional
      create_mask: "0660"            # optional
      directory_mask: "0770"         # optional
      enabled: true                  # optional, default true

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
          path: "zpool0/iscsi/vmstore"   # dataset path for zvol
          readonly: false
      auth:
        discovery_auth: "none"           # one of none|chap
        session_auth: "chap"             # one of none|chap
        chap_secret_ref: "iscsi/vmstore" # key in secrets file
      initiators:
        - "iqn.1993-08.org.debian:client1"
      enabled: true

garage:
  enabled: true
  runtime: "podman-quadlet-root"
  quadlet_name: "cloudyhome-garage"
  image: "dxflrs/garage:latest"
  rpc_bind: "10.0.0.10:3901"
  s3_bind: "10.0.0.10:3900"
  s3_region: "garage"
  replication_mode: "none"              # single-node default
  data_dir: "/zpool0/system/garage/data"
  metadata_dir: "/zpool0/system/garage/meta"
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

### 13.4 Validation Rules
- `version` must equal `1`.
- `storage.pool` must be `zpool0` for this deployment.
- `storage.datasets` must be a non-empty list of unique dataset paths.
- Each dataset entry must start with either `/zpool0` (mount path) or `zpool0/` (dataset name).
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
- `iscsi.targets[*].name` must be unique.
- `iscsi.targets[*].luns[*].lun` must be unique per target.
- `garage.enabled=true` requires:
  - `runtime=podman-quadlet-root`
  - non-empty `quadlet_name`
  - non-empty `image`
  - both `admin_token_ref` and `rpc_secret_ref`
- `ftp.enabled=true` requires:
  - `runtime=podman-quadlet-root`
  - non-empty `quadlet_name`
  - `image=delfer/alpine-ftp-server:latest` (or approved pinned tag)
  - `control_port=21` unless explicitly overridden
  - valid passive range (`passive_ports.min <= passive_ports.max`)
  - `upload_root` under `/zpool0/`
  - `users_ref` present and resolvable in secrets
- `ftp.tls.enabled=true` requires both `tls.cert_path` and `tls.key_path`.
- Any `*_ref` key must resolve in decrypted secrets file; unresolved refs fail closed.

### 13.5 Secrets Mapping Contract

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
      password_hash: "REDACTED"
ftp:
  users:
    - username: "scanner1"
      password: "REDACTED"
```

Rules:
- References are resolved as slash-delimited paths (example: `garage/admin_token`).
- Renderer must fail if a referenced key is absent.
- Renderer must not print resolved secret values in logs.
