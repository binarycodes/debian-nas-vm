# NAS Migration Architecture Plan

## Table of Contents

- [1. Problem Statement](#1-problem-statement)
- [2. Objectives](#2-objectives)
- [3. Why Proxmox + Single Debian VM](#3-why-proxmox--single-debian-vm)
- [4. High-Level Design](#4-high-level-design)
  - [4.1 Platform Layout](#41-platform-layout)
  - [4.2 Configuration Inputs](#42-configuration-inputs)
  - [4.3 Generated Runtime Outputs](#43-generated-runtime-outputs)
- [5. Boot Workflow](#5-boot-workflow)
- [6. Systemd Design](#6-systemd-design)
  - [6.1 cloudyhome-nas-validate.service](#61-cloudyhome-nas-validateservice)
  - [6.2 cloudyhome-zfs-import.service](#62-cloudyhome-zfs-importservice)
  - [6.3 cloudyhome-nas-render.service](#63-cloudyhome-nas-renderservice)
  - [6.4 cloudyhome-nas-firewall.service](#64-cloudyhome-nas-firewallservice)
  - [6.5 cloudyhome-nas-apply.service](#65-cloudyhome-nas-applyservice)
  - [6.6 cloudyhome-garage-bootstrap.service](#66-cloudyhome-garage-bootstrapservice)
- [7. Script Design (nas-render-config)](#7-script-design-nas-render-config)
- [8. Data Model (Template Contract)](#8-data-model-template-contract)
- [9. Security Model](#9-security-model)
- [10. Firewall Port Reference](#10-firewall-port-reference)
- [11. Operations and Lifecycle](#11-operations-and-lifecycle)
- [12. Implementation Plan](#12-implementation-plan)
- [13. Open Decisions](#13-open-decisions)
  - [13.1 Decided Constraints](#131-decided-constraints)
  - [13.2 Dataset and zvol Creation](#132-dataset-and-zvol-creation)
- [14. Finalized services.yml Structure](#14-finalized-servicesyml-structure)
  - [14.1 Design Principles](#141-design-principles)
  - [14.2 Top-Level Keys](#142-top-level-keys)
  - [14.3 Canonical Schema (Field Contract)](#143-canonical-schema-field-contract)
  - [14.4 Validation Rules](#144-validation-rules)
  - [14.5 Secrets Mapping Contract](#145-secrets-mapping-contract)

---

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
  - Includes ZFS, NFS, Samba, iSCSI tooling, Podman, cloud-init, sops, age, yq, nftables, Python 3.
  - Includes bootstrap scripts and systemd units.

### 4.2 Configuration Inputs
- Non-secret declarative template (baked in image):
  - `/var/lib/cloudyhome/nas/services.yml`
- Secret encrypted file (baked in image):
  - `/var/lib/cloudyhome/nas/secrets.enc.yaml`
- AGE private key (cloud-init injected):
  - Delivered via cloud-init `write_files` to `/etc/sops/age/keys.txt` at first boot.
  - SOPS does **not** auto-discover this path. All scripts and systemd units that invoke SOPS must set `SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt` explicitly (via `Environment=` in the unit or an export in the shell script).
  - The image contains encrypted secrets but never the decryption key.

### 4.3 Generated Runtime Outputs
- `/etc/nftables.conf` (firewall)
- `/etc/exports.d/cloudyhome.exports` (NFS)
- `/etc/samba/smb.conf` (Samba)
- `/etc/target/saveconfig.json` (iSCSI)
- `<garage.config_dir>/garage.toml` (Garage container config input; path driven by `garage.config_dir` in `services.yml`)
- `<ftp.config_dir>/ftp.env` (FTP container environment; path driven by `ftp.config_dir` in `services.yml`)
- `/etc/containers/systemd/cloudyhome-garage.container` (root Quadlet)
- `/etc/containers/systemd/cloudyhome-ftp.container` (root Quadlet)

All outputs are generated atomically from template + decrypted secrets at boot.

## 5. Boot Workflow

1. `cloud-init` finishes; AGE private key is present at `/etc/sops/age/keys.txt` (written via `write_files`).
2. `cloudyhome-nas-validate.service`:
   - Decrypts secrets and validates all fields in `services.yml` and `secrets.enc.yaml` against the full schema.
   - Exits 0 on success. Exits non-zero on any validation failure, stopping the entire boot chain.
   - All downstream services require this step to succeed before starting.
3. `cloudyhome-zfs-import.service`:
   - Verifies all disk IDs from `secrets.enc.yaml` are present under `/dev/disk/by-id/`; aborts with error if any are missing.
   - Checks whether `zpool0` is imported; imports it if needed.
   - Runs `zfs mount -a` to ensure all datasets are mounted (idempotent on reboot).
4. `cloudyhome-nas-render.service`:
   - Decrypts secrets from baked SOPS file using AGE key at `/etc/sops/age/keys.txt`.
   - Merges secret + non-secret data.
   - Renders firewall/NFS/Samba/iSCSI/Garage/FTP configs into `/etc`.
   - Validates generated config syntax.
   - Decrypted material cleaned from `/run` automatically on exit (guaranteed cleanup regardless of success or failure).
5. `cloudyhome-nas-firewall.service`:
   - Loads `/etc/nftables.conf` via `nft -f`.
   - Firewall is active before any NAS service starts.
6. `cloudyhome-nas-apply.service`:
   - Creates missing datasets and zvols.
   - Provisions Samba users into `tdbsam`.
   - Applies/loads iSCSI config.
   - Starts/reloads NFS, Samba, iSCSI, Garage, and FTP services.
7. `cloudyhome-garage-bootstrap.service`:
   - Runs after `cloudyhome-garage.service` is up.
   - Checks Garage layout via admin API; assigns and applies layout if not yet configured.

## 6. Systemd Design

## 6.1 `cloudyhome-nas-validate.service`
- Type: `oneshot`
- `After=cloud-init.target`
- `Requires=cloud-init.target`
- `RuntimeDirectory=nas`
- `Environment=SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt`
- `WantedBy=multi-user.target`
- Runs `/usr/local/sbin/nas-validate-config` (Python).
- Purpose: validate all fields in `services.yml` and `secrets.enc.yaml` before any other boot step runs. If validation fails, the service exits non-zero and all downstream services that `Requires=` it will not start.
- Decrypts `/var/lib/cloudyhome/nas/secrets.enc.yaml` using the AGE key at `/etc/sops/age/keys.txt`. Decrypted material cleaned from `/run` on exit (guaranteed cleanup regardless of success or failure).
- Checks that `/run` is mounted as tmpfs before any decryption occurs. If not, exits non-zero immediately — the security model depends on decrypted material never being written to persistent storage.
- Runs the full schema validation from Section 14.4 against the merged `services.yml` + decrypted secrets.
- Emits a clear log message for every validation failure before exiting non-zero.
- On success, exits 0 and produces no output beyond a single confirmation log line.

## 6.2 `cloudyhome-zfs-import.service`
- Type: `oneshot`
- `After=sysinit.target cloudyhome-nas-validate.service`
- `Requires=cloudyhome-nas-validate.service`
- `RuntimeDirectory=nas`
- `Environment=SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt`
- `WantedBy=multi-user.target`
- Purpose: import `zpool0` if not already imported, then mount all datasets.
- Runs `/usr/local/sbin/nas-zfs-import` (shell).
- The stock ZFS import services (`zfs-import-cache.service`, `zfs-import-scan.service`, `zfs-mount.service`, `zfs-share.service`) are masked in the Packer image. `cloudyhome-zfs-import.service` is the sole ZFS bootstrap mechanism.
- **Disk presence check** (runs first, before any ZFS operation): decrypts `secrets.enc.yaml` and reads `disks.ids[]`. For each entry, verifies that `/dev/disk/by-id/<id>` exists. If any disk is absent, logs the missing IDs and exits non-zero immediately. This catches VM disk passthrough misconfiguration before touching ZFS.
- If the pool is already imported: skip import, run `zfs mount -a` only.
- If the pool is found and importable: import it (which auto-mounts via ZFS mountpoint properties), then run `zfs mount -a` to catch any unmounted datasets.
- If the pool is not found: exit cleanly. Pool creation is a manual, out-of-band operation — never attempted here. Downstream services that require ZFS paths will fail naturally if the pool is absent.

## 6.3 `cloudyhome-nas-render.service`
- Type: `oneshot`
- `After=cloudyhome-nas-validate.service cloudyhome-zfs-import.service`
- `Requires=cloudyhome-nas-validate.service`
- `WantedBy=multi-user.target`
- `RuntimeDirectory=nas` — systemd creates `/run/nas/` before the script starts and removes it after exit. Required for `flock /run/nas/render.lock`.
- `Environment=SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt`
- Runs `/usr/local/sbin/nas-render-config`.

## 6.4 `cloudyhome-nas-firewall.service`
- Type: `oneshot`
- `After=cloudyhome-nas-render.service`
- `Requires=cloudyhome-nas-render.service`
- `Before=cloudyhome-nas-apply.service`
- `RuntimeDirectory=nas`
- `WantedBy=multi-user.target`
- Loads `/etc/nftables.conf` via `nft -f /etc/nftables.conf`.
- Stateful firewall: established/related connections auto-allowed.
- Loopback traffic always permitted.
- Default input policy: drop (as specified in `services.yml`).
- Firewall is fully active before any NAS service starts.

## 6.5 `cloudyhome-nas-apply.service`
- Type: `oneshot`
- `After=cloudyhome-nas-render.service cloudyhome-nas-firewall.service cloudyhome-zfs-import.service`
- `Requires=cloudyhome-nas-render.service cloudyhome-nas-firewall.service`
- `RuntimeDirectory=nas`
- `Environment=SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt`
- `WantedBy=multi-user.target`
- Runs `/usr/local/sbin/nas-apply-config` (Python).
- Acquires lock (`flock /run/nas/apply.lock`) to avoid concurrent runs.
- Performs apply/reload behavior via explicit `systemctl start` and `systemctl reload-or-restart` calls — not via `Wants=` or `Before=` dependencies.
- Decrypts `/var/lib/cloudyhome/nas/secrets.enc.yaml` to a temp file under `/run` using the AGE key at `/etc/sops/age/keys.txt`. Temp file is removed on exit (guaranteed cleanup regardless of success or failure).
- **ZFS dependency note**: `cloudyhome-zfs-import.service` exits 0 whether or not the pool was found (pool creation is always out-of-band). `Requires=` is therefore not used — it would be satisfied regardless of pool state. `After=` is used to guarantee ordering only. Steps 1 and 3–10 below are safe to run without ZFS. Step 2 requires ZFS and will log errors and skip individual dataset/zvol entries that fail; it must not abort the entire service. Downstream services that depend on ZFS paths will fail naturally if the pool is absent.
- Service interaction order:
  1. `systemctl daemon-reload` — required so systemd sees the Quadlet `.container` files rendered by `cloudyhome-nas-render.service`; must run before any Quadlet unit is started.
  2. Create missing datasets and zvols. Each `zfs create` is attempted independently; failures are logged and skipped without aborting the remaining steps.
  3. Create missing Samba OS users: for each `smb_`-prefixed user in `samba.users[]` in secrets, run `useradd --no-create-home --shell /usr/sbin/nologin <username>` if the account does not already exist. Idempotent.
  4. Provision Samba users into `tdbsam` via `smbpasswd`/`pdbedit`.
  5. `systemctl reload-or-restart nfs-server.service`
  6. `systemctl reload-or-restart smbd.service`
  7. `systemctl restart target.service` (iSCSI — full restart to apply new saveconfig.json)
  8. `systemctl start cloudyhome-garage.service`
  9. `systemctl start cloudyhome-ftp.service`
  10. `systemctl start cloudyhome-garage-bootstrap.service` — explicit trigger; consistent with the design of driving all startup from this service.

## 6.6 `cloudyhome-garage-bootstrap.service`
- Type: `oneshot`
- `After=cloudyhome-garage.service`
- `Wants=cloudyhome-garage.service`
- `RuntimeDirectory=nas`
- `Environment=SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt`
- No `WantedBy=` — intentionally omitted. This service is not auto-started by systemd; it is driven exclusively by `cloudyhome-nas-apply.service` via `systemctl start cloudyhome-garage-bootstrap.service` (step 10 of Section 6.5).
- Runs `/usr/local/sbin/nas-garage-bootstrap` (Python).
- Purpose: idempotent Garage layout assignment.
- Decrypts `/var/lib/cloudyhome/nas/secrets.enc.yaml` to a temp file under `/run` using the AGE key at `/etc/sops/age/keys.txt`. Temp file is removed on exit (guaranteed cleanup regardless of success or failure).
- All `garage` CLI calls are executed via `podman exec cloudyhome-garage garage ...` — no host-side Garage binary required.
- **Readiness wait**: before any other API call, polls `GET /v1/status` in a loop (up to 30 attempts, 1s sleep between each). All admin API calls include the admin token (resolved from `admin_token_ref`) as an authentication header. Exits non-zero if Garage does not become ready within the timeout. This handles the gap between the container process starting and the Garage HTTP server accepting requests.
- Obtains the local node ID from the `GET /v1/status` response — no separate CLI call needed.
- Queries `GET /v1/layout`; reads the current layout `version` from the response. If the current layout has no roles assigned, runs:
  1. `podman exec cloudyhome-garage garage layout assign -z garage -c <capacity> <node-id>`
  2. `podman exec cloudyhome-garage garage layout apply --version <current_version + 1>`
- If roles are already present in the API response, exits successfully without making changes.
- Reads `capacity` from `/var/lib/cloudyhome/nas/services.yml` (`garage.layout_capacity`). `services.yml` is not encrypted and is read directly.
- Single-node deployment: only one node ID is expected.

## 7. Script Design (`nas-render-config`)

Core behavior:
- Acquire lock (`flock /run/nas/render.lock`) to avoid concurrent runs.
- Decrypt `/var/lib/cloudyhome/nas/secrets.enc.yaml` to a temp file under `/run` using SOPS. The environment variable `SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt` must be set before invoking `sops` (provided via `Environment=` in the systemd unit; shell scripts must also export it explicitly). Temp file is removed on exit (guaranteed cleanup regardless of success or failure).
- Merge `/var/lib/cloudyhome/nas/services.yml` + decrypted secrets.
- Render target files with temp-file + validate + atomic move. Order matters:
  1. `mkdir -p` the destination directory for every output file before writing. Idempotent; applies to all render targets as several destination directories do not pre-exist on a fresh Debian image: `/etc/exports.d/`, `/etc/target/`, `/etc/containers/systemd/`, and all `config_dir` paths under `/etc/cloudyhome/`.
  2. Write output to a temp file in `/run/nas/`.
  3. Run validation against the temp file (e.g. `testparm -s <tempfile>`).
  4. Only on validation success: compare the temp file against the existing destination using `filecmp.cmp(tmp, dest, shallow=False)` (Python) or `cmp -s` (shell). If the content is identical, discard the temp file. If different (or the destination does not yet exist), `chmod`/`chown` the temp file, then `mv` it atomically to the final `/etc` path.
  5. On validation failure: delete the temp file and exit non-zero. The live `/etc` file is never touched.
- For each enabled container service (`garage`, `ftp`), also render the Quadlet `.container` file into `/etc/containers/systemd/` using the same temp-file + compare + atomic-move pattern. These files are generated from Pydantic-validated input and do not require a separate runtime validator.
- `testparm -s` validates the Samba config; `nft -c -f <tempfile>` validates the nftables config as a dry-run before promotion to `/etc/nftables.conf`. All other outputs are trusted from Pydantic-validated input and do not require a separate runtime validator.
- Set permissions:
  - restrictive mode for files containing credentials.
- Exit non-zero on any failed validation.

Idempotency (hard requirement):
- Every render and apply step must be safe to run on every boot, including reboots with no config change.
- Do not rewrite unchanged configs unnecessarily. The `filecmp.cmp(tmp, dest, shallow=False)` comparison in render step 4 ensures the destination file is only replaced when content has actually changed. Service reloads in the apply script are unconditional — these are boot-time services with no active client connections at that point, so unconditional reload is safe and correct.
- Samba user provisioning, iSCSI restore, dataset creation, and service reloads must all handle already-current state gracefully.
- Any step that fails idempotency is a bug.

## 8. Data Model (Template Contract)

`services.yml` defines non-secret structure:
- Firewall rules (port, protocol, sources_ref)
- NFS export paths/options
- Samba global + shares
- iSCSI target/LUN mapping
- Garage network and non-secret parameters
- FTP listeners, passive port range, and upload policy

`secrets.enc.yaml` defines sensitive values:
- Disk IDs for passthrough verification (`disks/ids`)
- NAS VM host IP (`host/ip`)
- Firewall source IP lists (per service)
- NFS client CIDRs (per export)
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
- Any service that needs secrets decrypts independently: SOPS → temp file under `/run` (tmpfs) → use → guaranteed cleanup on exit. No service depends on another's decryption output.
- Decrypted temp files exist only for the lifetime of the process that created them; removed unconditionally on exit.
- Restrictive ownership and modes on generated `/etc` files.
- Journald/logging avoids printing secret values.

Risk notes:
- `/etc/sops/age/keys.txt` persists on the VM filesystem for the lifetime of the VM; access must be restricted to root (`0600`).
- The cloud-init `write_files` entry for the AGE key must set `permissions: '0600'` and `owner: root:root`. This is enforced as a mandatory item in `PACKER_CHECKLIST.md`.
- `discovery_auth: "none"` on iSCSI means any host that can reach TCP 3260 can enumerate target IQNs. Mitigated by restricting port 3260 at the firewall to known initiator subnets only. Session CHAP protects actual data access regardless.

## 10. Firewall Port Reference

All ports are on the NAS VM. Actual rules are declared in `services.yml` (`firewall.rules`) and rendered to `/etc/nftables.conf` at boot. The table below is the reference for expected port usage; source restrictions must be explicitly configured per rule.

| Port(s)       | Protocol | Service         | Source restriction              | Notes                                               |
|---------------|----------|-----------------|---------------------------------|-----------------------------------------------------|
| 22            | TCP      | SSH             | Admin hosts only                | VM management access                                |
| 2049          | TCP      | NFS             | NFS client subnet               | NFSv4 only; TCP only, no UDP required               |
| 139           | TCP      | Samba (NetBIOS) | LAN subnet                      | Legacy NetBIOS session; not needed for SMB2/3 only  |
| 445           | TCP      | Samba (SMB)     | LAN subnet                      | Primary Samba port for SMB2/3                       |
| 3260          | TCP      | iSCSI           | Initiator subnet only           | Both discovery and session traffic; restrict tightly to prevent target enumeration by untrusted hosts |
| 3900          | TCP      | Garage S3       | S3 client subnet                | Garage S3-compatible object storage API             |
| 3901          | TCP      | Garage RPC      | No rule — blocked by default-drop | Single-node: no external RPC needed; bound to host IP, no firewall rule |
| 3903          | TCP      | Garage admin    | No rule — blocked by default-drop | Admin API bound to host IP; blocked by firewall default-drop |
| 21            | TCP      | FTP control     | Scanner IP only                 | Pure FTP control channel for scanner uploads        |
| 21000–21010   | TCP      | FTP passive     | Scanner IP only                 | Passive data channels; range defined in services.yml |

## 11. Operations and Lifecycle

Build/deploy model:
1. Build Debian image with Packer (existing, out of scope).
2. Provision/update VM with Terraform (existing, out of scope).
3. On first boot, cloud-init injects AGE key via `write_files`.
4. systemd imports pool, renders configs, starts services.

Deliverable scope (this project):
- Python validate script (`/usr/local/sbin/nas-validate-config`) — decrypts secrets, runs full schema validation against `services.yml` + `secrets.enc.yaml`, exits non-zero with clear error messages on any failure
- Shell ZFS import script (`/usr/local/sbin/nas-zfs-import`) — decrypts secrets via `sops -d` (requires `export SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt` at the top of the script) and extracts `disks.ids[]` using `yq` for the disk presence check; implements three-case ZFS import logic (already imported / importable / not found)
- Python renderer script (`/usr/local/sbin/nas-render-config`) — reads `services.yml` + decrypted secrets, validates, renders all configs into `/etc`
- Python apply script (`/usr/local/sbin/nas-apply-config`) — reads `services.yml` + decrypted secrets, creates datasets/zvols, provisions OS users and `tdbsam`, manages service lifecycle; Python for consistent YAML parsing with the renderer
- Python bootstrap script (`/usr/local/sbin/nas-garage-bootstrap`) — reads `services.yml` + decrypted secrets, polls Garage admin API for readiness, assigns and applies layout if not yet configured
- systemd units: `cloudyhome-nas-validate.service`, `cloudyhome-zfs-import.service`, `cloudyhome-nas-render.service`, `cloudyhome-nas-firewall.service`, `cloudyhome-nas-apply.service`, `cloudyhome-garage-bootstrap.service`
- Jinja2 templates for all generated configs (NFS exports, smb.conf, iSCSI saveconfig.json, garage.toml, ftp.env, Quadlet units)
- `services.yml` canonical example (baked into image by Packer)
- `secrets.enc.yaml` schema example (encrypted and baked into image by Packer)
- `PACKER_CHECKLIST.md` — mandatory Packer build steps (package installs, container image pre-pull, service disable, AGE key permissions)
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
   - Add validate, ZFS import, renderer, apply, and bootstrap scripts and systemd units.
2. Implement `nas-validate-config` and `cloudyhome-nas-validate.service`. The schema validation contract (Section 14.4) is the foundation for all subsequent steps — all other scripts rely on it passing before they run.
3. Implement systemd units and ordering.
4. Implement render logic for firewall first (required for all deployments). The render script validates its own generated output files (e.g. `nft -c -f`, `testparm -s`) independently of schema validation — both layers are required.
5. Add NFS and Samba generation.
6. Add iSCSI and Garage generation.
7. Add FTP generation.
8. Add idempotent restart policy.
9. Test:
   - clean boot
   - repeated boot
   - missing AGE key (`/etc/sops/age/keys.txt` absent)
   - missing or misconfigured disk passthrough (one or more disk IDs absent from `/dev/disk/by-id/`) — must error and exit, not proceed to ZFS operations
   - invalid template
   - invalid secrets
   - service-specific syntax errors
10. Perform full rebuild/recovery rehearsal.

## 13. Open Decisions

### 13.1 Decided Constraints
- **No credential persistence to ZFS.** All credentials are reprovisioned from secrets on every boot (including reboots). Config changes mean VM replacement, so secrets in the image are always current. All apply steps must be idempotent and safe to rerun.

- Container runtime is fixed: root Podman Quadlets for all containers.
- Render/config generation language: **Python**. Strict schema validation via `pydantic`; no config is written unless all validation passes. Libraries: `pyyaml`, `pydantic`, `jinja2`, `tomli-w`.
- iSCSI backend: **direct JSON generation**. The renderer builds `/etc/target/saveconfig.json` from `services.yml` + secrets. `target.service` (rtslib-fb) restores from it on boot. No `targetcli` interactive session involved.
- **NFS and Samba run as host services** (`nfs-kernel-server`, `smbd`), not containers. NFS is a kernel subsystem; containerizing it provides no isolation benefit and adds significant complexity. Samba follows the same decision for consistency. The VM is the isolation boundary. Garage and FTP remain containerized as Podman Quadlets.
- **NFS and Samba start handling**: `cloudyhome-nas-apply.service` uses `systemctl reload-or-restart` for both `nfs-server.service` and `smbd.service`. This is intentional — it correctly handles both cases: starts the service if stopped, reloads/restarts if already running. The apply service does not rely on or assume any prior state of these services. The Packer image may disable their auto-start as a best-effort measure, but the boot chain is correct regardless of whether they were pre-running or not.

### 13.2 Dataset and zvol Creation
During `cloudyhome-nas-apply.service`:
- **Datasets**: `storage.datasets` is a map of simple underscore-separated name to mount path (e.g. `shares_media: "/zpool0/shares/media"`). The apply script derives the ZFS dataset name from the mount path value by stripping the leading `/` (e.g. `zpool0/shares/media`) and passes it to `zfs create -p`. Parent datasets created automatically via `-p`. Existing datasets left untouched.
- **zvols** (iSCSI LUNs): created if missing using `zfs create -V <size> <path>`. Existing zvols left untouched (size is not modified). Path uses dataset name form (e.g. `zpool0/iscsi/vmstore`); block device is derived as `/dev/zvol/<path>`.

## 14. Finalized `services.yml` Structure

> **Note**: All field values, IPs, paths, usernames, ports, and secrets shown in this section are illustrative examples used to define the schema and contract. They are not the actual values the deployed system will use. Real values are supplied at deploy time via the actual `services.yml` and `secrets.enc.yaml` baked into the image.

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
- `host_ip_ref` (required, string): secret path resolving to the NAS VM's LAN IP; used as bind address for all services.
- `storage` (required, map): pool and dataset conventions.
- `firewall` (required, map): nftables rule definitions.
- `nfs` (optional, map): NFS export definitions.
- `samba` (optional, map): Samba global and share definitions.
- `iscsi` (optional, map): iSCSI IQN and LUN mapping.
- `garage` (optional, map): Garage process and S3 endpoint settings.
- `ftp` (optional, map): Pure FTP daemon settings for scanner uploads.

At least one of `nfs`, `samba`, `iscsi`, `garage`, or `ftp` must be present.

### 14.3 Canonical Schema (Field Contract)

```yaml
version: 1

host_ip_ref: "host/ip"              # resolves to the NAS VM's LAN IP from secrets; used as bind address for all services

storage:
  pool: "zpool0"
  datasets:                                                          # canonical inventory; created by cloudyhome-nas-apply.service if missing (zfs create -p)
    system:               "/zpool0/system"                          # NAS system/state parent dataset
    system_garage:        "/zpool0/system/garage"                   # Garage state parent dataset
    system_garage_data:   "/zpool0/system/garage/data"              # Garage data directory (bind-mounted into container)
    system_garage_meta:   "/zpool0/system/garage/meta"              # Garage metadata directory (bind-mounted into container)
    shares:               "/zpool0/shares"                          # file shares parent dataset
    shares_media:         "/zpool0/shares/media"                    # shared media (NFS + Samba)
    shares_scanner_inbox: "/zpool0/shares/scanner-inbox"            # FTP upload root
    iscsi:                "/zpool0/iscsi"                           # zvol parent dataset for iSCSI LUNs
    backups:              "/zpool0/backups"                         # backup target dataset (snapshots/replication landing)

firewall:
  default_input: "drop"              # drop | accept
  rules:
    - service: "ssh"
      ports: [22]
      proto: ["tcp"]
      sources_ref: "firewall/ssh"
    - service: "nfs"
      ports: [2049]
      proto: ["tcp"]
      sources_ref: "firewall/nfs"
    - service: "samba"
      ports: [139, 445]
      proto: ["tcp"]
      sources_ref: "firewall/samba"
    - service: "iscsi"
      ports: [3260]
      proto: ["tcp"]
      sources_ref: "firewall/iscsi"
    - service: "garage-s3"
      ports: [3900]
      proto: ["tcp"]
      sources_ref: "firewall/garage-s3"
    - service: "ftp"
      ports: [21]
      proto: ["tcp"]
      sources_ref: "firewall/ftp"
    - service: "ftp-passive"
      port_range: [21000, 21010]     # inclusive range; maps to nftables tcp dport 21000-21010
      proto: ["tcp"]
      sources_ref: "firewall/ftp"

nfs:
  version: 4                           # NFSv4 only; validated at boot. No rpcbind (port 111) or UDP required.
  exports:
    - name: "media"
      path: "/zpool0/shares/media"
      clients:
        - cidr_ref: "nfs/media"      # resolves to CIDR list in secrets
          options: ["rw", "sync", "no_subtree_check"]
          identity_map:
            mode: "root_squash"      # one of: root_squash|no_root_squash|all_squash
            anon_uid: null           # required when mode=all_squash
            anon_gid: null           # required when mode=all_squash
      options: []                    # optional export-level options appended to all clients
      enabled: true                  # optional, default true

samba:
  global:
    workgroup: "WORKGROUP"
    server_string: "CloudyHome NAS" # optional
  shares:
    - name: "media"
      path: "/zpool0/shares/media"
      browsable: true
      read_only: false
      guest_ok: false
      users_ref: ["smb_alice"]                  # usernames from samba.users[] in secrets; maps to valid_users in smb.conf
      write_list: []                        # optional
      force_user: ""                        # optional
      force_group: ""                       # optional
      create_mask: "0660"                   # optional
      directory_mask: "0770"                # optional
      enabled: true                         # optional, default true

iscsi:
  base_iqn: "iqn.2026-03.home.arpa:nas01"
  portal_port: 3260                    # bind IP resolved from host_ip_ref; single portal, single NIC — multipath not required for this deployment
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
  rpc_port: 3901                        # bind IP resolved from host_ip_ref
  s3_port: 3900                         # bind IP resolved from host_ip_ref
  admin_port: 3903                      # bind IP resolved from host_ip_ref; no firewall rule = blocked by default-drop
  s3_region: "garage"
  replication_mode: "none"              # single-node default
  config_dir: "/etc/cloudyhome/garage"  # host directory for rendered garage.toml; mounted read-only into container
  data_dir: "/zpool0/system/garage/data"
  metadata_dir: "/zpool0/system/garage/meta"
  layout_capacity: "1G"                 # capacity string passed to `garage layout assign`
  admin_token_ref: "garage/admin_token" # key in secrets file
  rpc_secret_ref: "garage/rpc_secret"   # key in secrets file

ftp:
  # TLS is intentionally not supported. This FTP instance is for internal scanner uploads only;
  # access is restricted to the scanner IP at the firewall. Plain FTP is acceptable in this context.
  enabled: true
  runtime: "podman-quadlet-root"
  quadlet_name: "cloudyhome-ftp"
  image: "delfer/alpine-ftp-server:latest"
  config_dir: "/etc/cloudyhome/ftp"     # host directory for rendered ftp.env; mounted into container
  # bind address resolved from top-level host_ip_ref; maps to ADDRESS env
  control_port: 21
  passive_ports:
    min: 21000                              # maps to MIN_PORT env
    max: 21010                              # maps to MAX_PORT env
  users_ref: "ftp/users"                    # maps to USERS env
  upload_root: "/zpool0/shares/scanner-inbox"
```

### 14.4 Validation Rules

**Global IP policy**: Every IP address or CIDR resolved anywhere in `services.yml` or `secrets.enc.yaml` must be a valid RFC1918 address (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`). This applies to `host_ip_ref`, all `sources_ref` lists, all `cidr_ref` lists, and any other IP value. Non-RFC1918 values fail validation regardless of where they appear.

- `disks.ids` in secrets must be a non-empty list of strings. Each entry is treated as a bare disk ID name; the script checks for `/dev/disk/by-id/<id>`. Empty list or absent key is a fatal error.
- `version` must equal `1`.
- `host_ip_ref` is required and must resolve to a valid RFC1918 IP address in secrets.
- `firewall` is required; omitting it is a validation error.
- `firewall.default_input` must be one of `drop` or `accept`.
- `firewall.rules` must be a non-empty list.
- `firewall.rules[*].service` must be unique.
- Each rule must have `service` (non-empty string), at least one of `ports` or `port_range`, `proto` (non-empty list), and `sources_ref` (non-empty string).
- `ports` entries must be valid port numbers in the range 1001–65535, except for well-known ports required by their protocol: SSH (22), rpcbind (111), Samba NetBIOS (139), Samba SMB (445), FTP control (21).
- `port_range` must be a two-element list `[min, max]` where `min <= max` and both values are in the range 1001–65535.
- `proto` entries must be one of `tcp` or `udp`.
- `sources_ref` must resolve to a non-empty list of IPs or CIDRs in secrets (RFC1918 enforced by global IP policy).
- A rule may not define both `ports` and `port_range`.
- `storage.pool` must be `zpool0` for this deployment.
- `storage.datasets` must be a non-empty map with unique keys and unique values.
- Each key must be a non-empty underscore-separated identifier (simple name, e.g. `shares_media`).
- Each value must be an absolute mount path starting with `/zpool0/`. The ZFS dataset name is derived by stripping the leading `/`.
- Any `path` intended for data export must start with `/zpool0/`.
- `nfs.version` must be `4`. NFSv3 is not supported in this deployment; any other value fails validation. This check is enforced in `cloudyhome-nas-validate.service` before the boot chain proceeds.
- `nfs.exports` must be a non-empty list when `nfs` is present.
- `nfs.exports[*].name` must be unique.
- `nfs.exports[*].path` must be unique.
- `nfs.exports[*].clients` must be non-empty when export is enabled.
- `nfs.exports[*].clients[*].cidr_ref` is required and must resolve to a non-empty CIDR list in secrets (RFC1918 enforced by global IP policy).
- `nfs.exports[*].clients[*].options` is optional; defaults to `[]`.
- `nfs.exports[*].clients[*].identity_map.mode` is optional; defaults to `root_squash`.
- `nfs.exports[*].clients[*].identity_map.mode` must be one of:
  - `root_squash`
  - `no_root_squash`
  - `all_squash`
- If `identity_map.mode=all_squash`, both `anon_uid` and `anon_gid` are required.
- `samba.shares` must be a non-empty list when `samba` is present.
- `samba.shares[*].name` must be unique.
- `samba.users[*].username` in secrets must be unique.
- `samba.shares[*].users_ref` must be a non-empty list of usernames. Every entry must exist in `samba.users[*].username` in secrets. Unresolved usernames fail closed.
- All Samba usernames (in both `users_ref` lists and `samba.users[*].username` in secrets) must be prefixed with `smb_`. This ensures Samba system accounts are clearly namespaced and cannot collide with other OS users.
- `iscsi.targets` must be a non-empty list when `iscsi` is present.
- `iscsi.base_iqn` is required when `iscsi` is present and must be a non-empty string in valid IQN format (`iqn.YYYY-MM.<domain>:<string>`).
- `iscsi.portal_port` is required when `iscsi` is present and must be a valid port number in the range 1001–65535.
- `iscsi.targets[*].name` must be unique.
- `iscsi.targets[*].iqn_suffix` must be non-empty.
- `iscsi.targets[*].iqn_suffix` must be unique across all targets (duplicate suffixes produce duplicate IQNs).
- `iscsi.targets[*].luns` must be a non-empty list.
- `iscsi.targets[*].luns[*].lun` must be unique per target.
- `iscsi.targets[*].luns[*].path` must be unique across all targets.
- `iscsi.targets[*].initiators` defaults to deny-all when empty. The renderer must not allow implicit open access — an empty list is valid and means no initiator is permitted.
- `iscsi.targets[*].auth.discovery_auth` must be one of `none` or `chap`.
- `iscsi.targets[*].auth.session_auth` must be one of `none` or `chap`.
- If `session_auth=chap`, `chap_secret_ref` is required and must resolve to a map with two non-empty string fields: `chap_user` and `chap_password`.
- `iscsi.targets[*].luns[*].type` must be `"zvol"`. No other LUN types are supported in this deployment.
- `iscsi.targets[*].luns[*].size` is required for `type=zvol` and must be a valid ZFS size string (e.g. `"100G"`).
- `iscsi.targets[*].luns[*].path` uses dataset name form (no leading slash); block device path is derived as `/dev/zvol/<path>`.
- `garage.enabled=true` requires:
  - `runtime=podman-quadlet-root`
  - non-empty `quadlet_name`
  - non-empty `image` (Garage container image is `dxflrs/garage`; version pinning is managed in the Quadlet file, not validated here)
  - both `admin_token_ref` and `rpc_secret_ref`
  - `admin_port` must be specified; no firewall rule is generated for it — blocked by default-drop
  - `config_dir` must be a non-empty absolute path; the renderer writes `garage.toml` into this directory and the Quadlet mounts it read-only into the container
  - `data_dir` must start with `/zpool0/`
  - `metadata_dir` must start with `/zpool0/`
  - `layout_capacity` must be non-empty.
  - `rpc_port` must be a valid port number in the range 1001–65535.
  - `s3_port` must be a valid port number in the range 1001–65535.
- `ftp.enabled=true` requires:
  - `runtime=podman-quadlet-root`
  - non-empty `quadlet_name`
  - `config_dir` must be a non-empty absolute path; the renderer writes `ftp.env` into this directory and the Quadlet mounts it into the container
  - non-empty `image` (FTP container image is `delfer/alpine-ftp-server`; version pinning is managed in the Quadlet file, not validated here)
  - `control_port` must be `21`. Hardware scanners expect the standard FTP control port; non-standard values are not supported in this deployment.
  - valid passive range (`passive_ports.min <= passive_ports.max`)
  - `upload_root` under `/zpool0/`
  - `users_ref` present and resolvable in secrets
- `ftp.users[*].username` in secrets must be unique.

### 14.5 Secrets Mapping Contract

`secrets.enc.yaml` is keyed by reference path used in `services.yml`:

```yaml
disks:
  ids:
    - "ata-WDC_WD40EFRX-68N32N0_WD-XXXXXXXX"   # disk 1 — stable by-id name, no /dev/disk/by-id/ prefix
    - "ata-WDC_WD40EFRX-68N32N0_WD-YYYYYYYY"   # disk 2
    - "ata-WDC_WD40EFRX-68N32N0_WD-ZZZZZZZZ"   # disk 3
    - "ata-WDC_WD40EFRX-68N32N0_WD-AAAAAAAA"   # disk 4
    - "ata-WDC_WD40EFRX-68N32N0_WD-BBBBBBBB"   # disk 5
    - "ata-WDC_WD40EFRX-68N32N0_WD-CCCCCCCC"   # disk 6

host:
  ip: "10.0.0.10"
firewall:
  ssh:       ["10.0.0.0/24"]
  nfs:       ["10.0.0.0/24"]
  samba:     ["10.0.0.0/24"]
  iscsi:     ["10.0.0.0/24"]
  garage-s3: ["10.0.0.0/24"]
  ftp:       ["192.168.1.50"]
nfs:
  media:     ["10.0.0.0/24"]
iscsi:
  vmstore:
    chap_user: "vmstore-user"
    chap_password: "REDACTED"
garage:
  admin_token: "REDACTED"
  rpc_secret: "REDACTED"
samba:
  users:
    - username: "smb_alice"
      password: "REDACTED"
# Note: Samba usernames must be prefixed with smb_ (e.g. smb_alice). The apply service creates
# the corresponding Unix system account before provisioning into tdbsam.
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
- User provisioning (writing to `tdbsam`) is handled in `cloudyhome-nas-apply.service`.
