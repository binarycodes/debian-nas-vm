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
  - [6.7 cloudyhome-zfs-scrub.service](#67-cloudyhome-zfs-scrubservice)
  - [6.8 cloudyhome-zfs-scrub.timer](#68-cloudyhome-zfs-scrubtimer)
  - [6.9 smartd.service (stock, configured)](#69-smartdservice-stock-configured)
  - [6.10 zfs-zed.service (stock, configured)](#610-zfs-zedservice-stock-configured)
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
- [15. Monitoring and Storage Health](#15-monitoring-and-storage-health)
  - [15.1 Overview](#151-overview)
  - [15.2 ZFS Scrub Schedule](#152-zfs-scrub-schedule)
  - [15.3 SMART Test Schedules](#153-smart-test-schedules)
  - [15.4 ZFS Event Daemon (ZED)](#154-zfs-event-daemon-zed)
  - [15.5 Alert Delivery](#155-alert-delivery)
  - [15.6 Alert Script](#156-alert-script)
  - [15.7 Render and Validation Integration](#157-render-and-validation-integration)
  - [15.8 Packer Image Requirements](#158-packer-image-requirements)
  - [15.9 Open Decisions](#159-open-decisions)
- [16. Source Tree Layout](#16-source-tree-layout)

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
  - Includes ZFS, NFS, Samba, iSCSI tooling, Podman, cloud-init, sops, age, yq, nftables, Python 3, smartmontools, zfs-zed, msmtp.
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
- `/etc/cloudyhome/health/alert.conf` (alert script config)
- `/etc/msmtprc` (SMTP relay config; only when `health.alert.enabled=true`)
- `/etc/cloudyhome/nas-apply-services.sh` (rendered service lifecycle script; contains systemctl commands for health monitoring (always) and NAS services present and enabled in `services.yml`)

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
   - If the pool is not found or cannot be imported, exits non-zero — the entire boot chain stops here.
   - Runs `zfs mount -a` to ensure all datasets are mounted (idempotent on reboot).
4. `cloudyhome-nas-render.service`:
   - Decrypts secrets from baked SOPS file using AGE key at `/etc/sops/age/keys.txt`.
   - Merges secret + non-secret data.
   - Renders firewall/NFS/Samba/iSCSI/Garage/FTP/health alert configs into `/etc`.
   - Validates generated config syntax.
   - Decrypted material cleaned from `/run` automatically on exit (guaranteed cleanup regardless of success or failure).
5. `cloudyhome-nas-firewall.service`:
   - Loads `/etc/nftables.conf` via `nft -f`.
   - Firewall is active before any NAS service starts.
6. `cloudyhome-nas-apply.service`:
   - Creates missing datasets and zvols; enforces dataset quotas.
   - Provisions Samba users into `tdbsam` (only if `samba` is configured).
   - Runs `/etc/cloudyhome/nas-apply-services.sh` — a rendered script that starts health monitoring services (always) and NAS services present and enabled in `services.yml`.
7. `cloudyhome-garage-bootstrap.service`:
   - Runs after `cloudyhome-garage.service` is up.
   - Checks Garage layout via admin API; assigns and applies layout if not yet configured.

## 6. Systemd Design

### 6.1 `cloudyhome-nas-validate.service`
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

### 6.2 `cloudyhome-zfs-import.service`
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
- If the pool is not found: exit non-zero. Pool creation is a manual, out-of-band operation — never attempted here. A missing pool is a fatal error because all downstream services depend on ZFS datasets. The boot chain stops at this point.

### 6.3 `cloudyhome-nas-render.service`
- Type: `oneshot`
- `After=cloudyhome-nas-validate.service cloudyhome-zfs-import.service`
- `Requires=cloudyhome-nas-validate.service cloudyhome-zfs-import.service`
- `WantedBy=multi-user.target`
- `RuntimeDirectory=nas` — systemd creates `/run/nas/` before the script starts and removes it after exit. Required for `flock /run/nas/render.lock`.
- `Environment=SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt`
- Runs `/usr/local/sbin/nas-render-config`.

### 6.4 `cloudyhome-nas-firewall.service`
- Type: `oneshot`
- `After=cloudyhome-nas-render.service`
- `Requires=cloudyhome-nas-render.service`
- `Before=cloudyhome-nas-apply.service`
- `WantedBy=multi-user.target`
- Loads `/etc/nftables.conf` via `nft -f /etc/nftables.conf`.
- Stateful firewall: established/related connections auto-allowed.
- Loopback traffic always permitted.
- Default input policy: drop (as specified in `services.yml`).
- Firewall is fully active before any NAS service starts.

### 6.5 `cloudyhome-nas-apply.service`
- Type: `oneshot`
- `After=cloudyhome-nas-render.service cloudyhome-nas-firewall.service cloudyhome-zfs-import.service`
- `Requires=cloudyhome-nas-render.service cloudyhome-nas-firewall.service cloudyhome-zfs-import.service`
- `RuntimeDirectory=nas`
- `Environment=SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt`
- `WantedBy=multi-user.target`
- Runs `/usr/local/sbin/nas-apply-config` (Python).
- Acquires lock (`flock /run/nas/apply.lock`) to avoid concurrent runs.
- Performs apply/reload behavior via explicit `systemctl start` and `systemctl reload-or-restart` calls — not via `Wants=` or `Before=` dependencies.
- Decrypts `/var/lib/cloudyhome/nas/secrets.enc.yaml` to a temp file under `/run` using the AGE key at `/etc/sops/age/keys.txt`. Temp file is removed on exit (guaranteed cleanup regardless of success or failure).
- Apply sequence:
  1. `systemctl daemon-reload` — required so systemd sees the Quadlet `.container` files rendered by `cloudyhome-nas-render.service`; must run before any Quadlet unit is started. Always runs.
  2. Create missing datasets and zvols, and enforce quotas. Each `zfs create` is attempted independently; any failure is fatal — the script logs the error and exits non-zero immediately without proceeding to subsequent steps. For each dataset, after creation (or if it already exists), the configured quota is enforced: if the configured quota differs from the current value, the script verifies current usage is below the configured quota before applying it; if usage meets or exceeds the configured quota, the script exits non-zero. Always runs.
  3. If `samba` is present in `services.yml`: create missing Samba OS users — for each `smb_`-prefixed key in `samba.users` map in secrets, run `useradd --no-create-home --shell /usr/sbin/nologin <username>` if the account does not already exist. Idempotent.
  4. If `samba` is present in `services.yml`: provision Samba users into `tdbsam` idempotently — for each entry in the `samba.users` map in secrets, check existence with `pdbedit -L -u <username>` (exit 0 = already present); if absent, add via `smbpasswd -a -s <username>` with password piped to stdin; if present, update password via `smbpasswd -s <username>` (no `-a`).
  5. Run `/etc/cloudyhome/nas-apply-services.sh` — the rendered service lifecycle script. Contains systemctl commands for health monitoring (always) and NAS services present and enabled in `services.yml` (see below). Always runs; the script itself is the conditional gate.

**`/etc/cloudyhome/nas-apply-services.sh`** — rendered by `cloudyhome-nas-render.service`:
- Generated from `services.yml`. Contains no secrets — only `systemctl` commands and the garage bootstrap trigger.
- Permissions: `0755 root:root`.
- **Health monitoring** (always included, unconditionally):
  - `systemctl reload-or-restart smartd.service`
  - `systemctl reload-or-restart zfs-zed.service`
  - `systemctl start cloudyhome-zfs-scrub.timer`
- **NAS services** (included only when the corresponding key is present in `services.yml` and the service is enabled). For `nfs`, `samba`, and `iscsi`, presence of the key means enabled (no service-level `enabled` field). For `garage` and `ftp`, the key must be present **and** `enabled=true`:
  - `nfs` → `systemctl reload-or-restart nfs-server.service`
  - `samba` → `systemctl reload-or-restart smbd.service`
  - `iscsi` → `systemctl restart target.service`
  - `garage` (enabled) → `systemctl start cloudyhome-garage.service`
  - `ftp` (enabled) → `systemctl start cloudyhome-ftp.service`
  - `garage` (enabled) → `systemctl start cloudyhome-garage-bootstrap.service` (after garage start)
- Config files (Quadlet `.container` files, `garage.toml`, `ftp.env`) are always rendered when the key is present, regardless of `enabled`. This allows inspection and manual start without a re-render. Only the apply script's NAS service start lines are gated on `enabled=true`.
- Rendered using the same temp-file + compare + atomic-move pattern as all other config files.
- The script always has health monitoring commands even if no NAS services are configured.

### 6.6 `cloudyhome-garage-bootstrap.service`
- Type: `oneshot`
- `After=cloudyhome-garage.service`
- `Wants=cloudyhome-garage.service`
- `RuntimeDirectory=nas`
- `Environment=SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt`
- No `WantedBy=` — intentionally omitted. This service is not auto-started by systemd; it is driven exclusively by `cloudyhome-nas-apply.service` via the rendered `nas-apply-services.sh` script (Section 6.5).
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

### 6.7 `cloudyhome-zfs-scrub.service`
- Type: `oneshot`
- `After=cloudyhome-zfs-import.service`
- No `WantedBy=` — driven exclusively by the timer.
- Runs: `zpool scrub zpool0`
- Purpose: periodic ZFS scrub to detect silent data corruption. Scrub results are reported by ZED (Section 15.4) on completion.

### 6.8 `cloudyhome-zfs-scrub.timer`
- `OnCalendar=*-*-1,15 02:00:00` — runs at 02:00 on the 1st and 15th of each month.
- `Persistent=true` — if the VM was off at the scheduled time, the scrub runs on next boot.
- `WantedBy=timers.target`
- The timer is enabled by `systemctl enable cloudyhome-zfs-scrub.timer` in the installer Makefile. Scrub frequency is a static image-time decision, not driven by `services.yml`.

### 6.9 `smartd.service` (stock, configured)
- Stock `smartmontools` service, enabled at runtime by `nas-apply-config` during the apply phase.
- Started by the rendered `nas-apply-services.sh` script (`systemctl reload-or-restart smartd.service`) — unconditionally, on every boot.
- Configured via static `/etc/smartd.conf` baked into the image (not rendered at boot).
- Runs scheduled SMART self-tests: short daily at 02:00, long Saturdays at 03:00.
- Monitors temperature and health attributes for all passed-through disks via `DEVICESCAN`.
- Alerts via `-M exec /usr/local/sbin/nas-health-alert` — calls the alert script with `SMARTD_DEVICE`, `SMARTD_FAILTYPE`, and `SMARTD_MESSAGE` environment variables.
- Does not depend on ZFS — it only needs disks to be present.
- Full config breakdown in Section 15.3.

### 6.10 `zfs-zed.service` (stock, configured)
- Stock ZFS Event Daemon service, enabled at runtime by `nas-apply-config` during the apply phase.
- Started by the rendered `nas-apply-services.sh` script (`systemctl reload-or-restart zfs-zed.service`) — unconditionally, on every boot.
- Configured via static `/etc/zfs/zed.d/zed.rc` baked into the image (not rendered at boot).
- Watches the ZFS kernel event stream and fires ZEDLETs on pool state changes, scrub results, I/O errors, and resilver completions.
- Built-in email delivery is disabled in `zed.rc`; alerts are routed through custom ZEDLET symlinks that call `/usr/local/sbin/nas-zedlet-wrapper` → `/usr/local/sbin/nas-health-alert`.
- Full config and ZEDLET details in Section 15.4.

## 7. Script Design (`nas-render-config`)

Core behavior:
- Acquire lock (`flock /run/nas/render.lock`) to avoid concurrent runs.
- Decrypt `/var/lib/cloudyhome/nas/secrets.enc.yaml` to a temp file under `/run` using SOPS. The environment variable `SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt` must be set before invoking `sops` (provided via `Environment=` in the systemd unit; shell scripts must also export it explicitly). Temp file is removed on exit (guaranteed cleanup regardless of success or failure).
- Merge `/var/lib/cloudyhome/nas/services.yml` + decrypted secrets.
- Render target files with temp-file + validate + atomic move. Order matters:
  1. `mkdir -p` the destination directory for every output file before writing. Idempotent; applies to all render targets as several destination directories do not pre-exist on a fresh Debian image: `/etc/exports.d/`, `/etc/target/`, `/etc/containers/systemd/`, `/etc/cloudyhome/health/`, and all `config_dir` paths under `/etc/cloudyhome/`.
  2. Write output to a temp file in `/run/nas/`.
  3. Run validation against the temp file (e.g. `testparm -s <tempfile>`).
  4. Only on validation success: compare the temp file against the existing destination using `filecmp.cmp(tmp, dest, shallow=False)`. If the content is identical, discard the temp file. If different (or the destination does not yet exist), `chmod`/`chown` the temp file, then `mv` it atomically to the final `/etc` path.
  5. On validation failure: delete the temp file and exit non-zero. The live `/etc` file is never touched.
- **Per-item enabled filtering**: NFS exports, Samba shares, and iSCSI targets each have an `enabled` field (optional, default `true`). Items with `enabled=false` are excluded from the rendered config file entirely — they do not appear in `/etc/exports.d/cloudyhome.exports`, `/etc/samba/smb.conf`, or `/etc/target/saveconfig.json` respectively. This is enforced at the render level because NFS, Samba, and iSCSI have no native per-item enable/disable mechanism. Disabled items remain in `services.yml` for documentation and can be re-enabled without rewriting the schema.
- For each container service (`garage`, `ftp`) whose key is present in `services.yml`, render the Quadlet `.container` file into `/etc/containers/systemd/` using the same temp-file + compare + atomic-move pattern, regardless of the `enabled` field. These files are generated from Pydantic-validated input and do not require a separate runtime validator.
- `testparm -s` validates the Samba config; `nft -c -f <tempfile>` validates the nftables config as a dry-run before promotion to `/etc/nftables.conf`. All other outputs are trusted from Pydantic-validated input and do not require a separate runtime validator.
- Permissions are set on the temp file before the atomic move (step 4 above); `mv` preserves them:
  - `0600 root:root`: `garage.toml`, `ftp.env`, `/etc/target/saveconfig.json`, `/etc/msmtprc`, `/etc/cloudyhome/health/alert.conf` (contain resolved secret values)
  - `0755 root:root`: `/etc/cloudyhome/nas-apply-services.sh` (rendered script, no secret content)
  - `0644 root:root`: `/etc/nftables.conf`, `/etc/exports.d/cloudyhome.exports`, `/etc/samba/smb.conf`, Quadlet `.container` files (no secret content)
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
- Health alert settings (SMTP host, port, TLS mode)

`secrets.enc.yaml` defines sensitive values:
- Disk IDs for passthrough verification (`disks.ids`)
- NAS VM host IP (`host/ip`)
- Firewall source IP lists (per service)
- NFS client CIDRs (per export)
- Samba users/passwords
- iSCSI CHAP credentials
- Garage RPC secret and admin token
- FTP local/virtual account credentials
- SMTP relay credentials (health alerting)
- Alert email addresses (health alerting)
- Allowed email domains (`allowed_email_domains`; enforced globally across all email fields)

Renderer contract:
- Strict schema validation before writing `/etc`.
- Fail closed on missing required secret fields.

## 9. Security Model

Controls:
- AGE private key delivered via cloud-init `write_files` — never in process args, environment, or shell history.
- Any service that needs secrets decrypts independently: SOPS → temp file under `/run` (tmpfs) → use → guaranteed cleanup on exit. No service depends on another's decryption output.
- Decrypted temp files exist only for the lifetime of the process that created them; removed unconditionally on exit.
- Restrictive ownership and modes on generated `/etc` files (files containing resolved secrets — `garage.toml`, `ftp.env`, `saveconfig.json`, `msmtprc`, `alert.conf` — are `0600 root:root`).
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
| 445           | TCP      | Samba (SMB)     | LAN subnet                      | SMB2/3 only; port 139 (NetBIOS) not used            |
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
- Shell ZFS import script (`/usr/local/sbin/nas-zfs-import`) — decrypts secrets via `sops -d` (requires `export SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt` at the top of the script) and extracts `disks.ids[]` using `yq` for the disk presence check; implements ZFS import logic (already imported / importable / not found — fatal error)
- Python renderer script (`/usr/local/sbin/nas-render-config`) — reads `services.yml` + decrypted secrets, validates, renders all configs into `/etc`
- Python apply script (`/usr/local/sbin/nas-apply-config`) — reads `services.yml` + decrypted secrets, creates datasets/zvols, provisions OS users and `tdbsam`, manages service lifecycle; Python for consistent YAML parsing with the renderer
- Python bootstrap script (`/usr/local/sbin/nas-garage-bootstrap`) — reads `services.yml` + decrypted secrets, polls Garage admin API for readiness, assigns and applies layout if not yet configured
- Shell alert script (`/usr/local/sbin/nas-health-alert`) — logs to journal, sends email via msmtp when enabled; called by both smartd and ZED
- Shell ZEDLET wrapper (`/usr/local/sbin/nas-zedlet-wrapper`) — forwards ZED environment variables and calls the alert script
- systemd units: `cloudyhome-nas-validate.service`, `cloudyhome-zfs-import.service`, `cloudyhome-nas-render.service`, `cloudyhome-nas-firewall.service`, `cloudyhome-nas-apply.service`, `cloudyhome-garage-bootstrap.service`, `cloudyhome-zfs-scrub.service`, `cloudyhome-zfs-scrub.timer`
- Jinja2 templates for all generated configs (NFS exports, smb.conf, garage.toml, ftp.env, Quadlet units, alert.conf, msmtprc, nas-apply-services.sh); iSCSI saveconfig.json is generated directly in code (not a template)
- Static config files baked into image: `/etc/smartd.conf`, `/etc/zfs/zed.d/zed.rc`
- `services.yml` canonical example (baked into image by Packer)
- `secrets.enc.yaml` schema example (encrypted and baked into image by Packer)
- `PACKER_CHECKLIST.md` — mandatory Packer build steps (package installs, container image pre-pull, AGE key permissions)
- All files are placed under `nas_root/` which Packer copies into the image, then runs `make -C /var/lib/cloudyhome/installer install`
- The installer Makefile (`var/lib/cloudyhome/installer/Makefile`) handles all post-copy setup: installing the `cloudyhome` Python package, setting script permissions, `systemctl enable` for all custom units, masking/disabling stock services, and creating ZEDLET symlinks. Stock services (`smartd.service`, `zfs-zed.service`) are enabled at runtime by `nas-apply-config`.

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
8. Add health alert rendering (`alert.conf`, `msmtprc`) and baked configs (`smartd.conf`, `zed.rc`, ZEDLET symlinks, alert script, scrub timer).
9. Add idempotent restart policy.
10. Test:
   - clean boot
   - repeated boot
   - missing AGE key (`/etc/sops/age/keys.txt` absent)
   - missing or misconfigured disk passthrough (one or more disk IDs absent from `/dev/disk/by-id/`) — must error and exit, not proceed to ZFS operations
   - invalid template
   - invalid secrets
   - service-specific syntax errors
   - health alerting with `ALERT_ENABLED=true` (email delivery) and `ALERT_ENABLED=false` (journal only)
11. Perform full rebuild/recovery rehearsal.

## 13. Open Decisions

### 13.1 Decided Constraints
- **No credential persistence to ZFS.** All credentials are reprovisioned from secrets on every boot (including reboots). Config changes mean VM replacement, so secrets in the image are always current. All apply steps must be idempotent and safe to rerun.

- Container runtime is fixed: root Podman Quadlets for all containers.
- Render/config generation language: **Python**. Strict schema validation via `pydantic`; no config is written unless all validation passes. Libraries: `pyyaml`, `pydantic`, `jinja2`.
- iSCSI backend: **direct JSON generation**. The renderer builds `/etc/target/saveconfig.json` from `services.yml` + secrets. `target.service` (rtslib-fb) restores from it on boot. No `targetcli` interactive session involved.
- **NFS and Samba run as host services** (`nfs-kernel-server`, `smbd`), not containers. NFS is a kernel subsystem; containerizing it provides no isolation benefit and adds significant complexity. Samba follows the same decision for consistency. The VM is the isolation boundary. Garage and FTP remain containerized as Podman Quadlets.
- **NFS, Samba, and iSCSI start handling**: the rendered `nas-apply-services.sh` script uses `systemctl reload-or-restart` for `nfs-server.service` and `smbd.service`, and `systemctl restart` for `target.service`, but only when the corresponding service is present in `services.yml`. This correctly handles both cases: starts the service if stopped, reloads/restarts if already running. The apply chain does not rely on or assume any prior state of these services. The Packer image disables their auto-start as a best-effort measure, but the boot chain is correct regardless of whether they were pre-running or not. If `nfs`, `samba`, or `iscsi` is absent from `services.yml`, the corresponding `systemctl` command is not included in the rendered script and the service is never started.

### 13.2 Dataset and zvol Creation
During `cloudyhome-nas-apply.service`:
- **Datasets**: `storage.datasets` is a map of simple underscore-separated name to an object with `path` (mount path) and `quota` (ZFS quota) fields (e.g. `shares_media: {path: "/zpool0/shares/media", quota: "4T"}`). The ZFS dataset name in the config is the map key (e.g. `shares_media`). The apply script constructs the full ZFS name as `{storage.pool}/{key}` (e.g. `zpool0/shares_media`) and creates it via `zfs create -o mountpoint=<path> {pool}/{key}`. After creation, the quota is set via `zfs set quota=<quota> {pool}/{key}`. For existing datasets, the script reads the current quota with `zfs get -Hp -o value quota {pool}/{key}` and compares it to the configured value: if the current quota differs from the configured value (or is `none`), the script checks current usage via `zfs get -Hp -o value used {pool}/{key}`. If usage is below the configured quota, the quota is set via `zfs set quota=<quota> {pool}/{key}` (works for both increases and decreases). If usage already meets or exceeds the configured quota, the script logs an error and exits non-zero — applying the quota would make the dataset immediately full.
- **zvols** (iSCSI LUNs): created if missing. The config path omits the pool prefix (e.g. `iscsi/vmstore`); the apply script prepends `storage.pool` to get the full ZFS name (`zpool0/iscsi/vmstore`) and runs `zfs create -V <size> {pool}/{path}`. Block device is derived as `/dev/zvol/{pool}/{path}`. Existing zvols left untouched (size is not modified).

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
- `storage` (required, map): pool, dataset conventions, and per-dataset quotas.
- `firewall` (required, map): nftables rule definitions.
- `nfs` (optional, map): NFS export definitions.
- `samba` (optional, map): Samba global and share definitions.
- `iscsi` (optional, map): iSCSI IQN and LUN mapping.
- `garage` (optional, map): Garage process and S3 endpoint settings.
- `ftp` (optional, map): Pure FTP daemon settings for scanner uploads.
- `health` (optional, map): Storage health monitoring and alert delivery settings.

All of `nfs`, `samba`, `iscsi`, `garage`, and `ftp` are optional. A config with none of them is valid — the boot chain runs, the firewall loads, and no NAS services are started.

### 14.3 Canonical Schema (Field Contract)

```yaml
version: 1

host_ip_ref: "host/ip"              # resolves to the NAS VM's LAN IP from secrets; used as bind address for all services

storage:
  pool: "zpool0"
  datasets:                                                          # canonical inventory; created by cloudyhome-nas-apply.service if missing; ZFS dataset name = map key
    system:                                                          # NAS system/state parent dataset
      path: "/zpool0/system"
      quota: "10G"
    system_garage:                                                   # Garage state parent dataset
      path: "/zpool0/system/garage"
      quota: "50G"
    system_garage_data:                                              # Garage data directory (bind-mounted into container)
      path: "/zpool0/system/garage/data"
      quota: "500G"
    system_garage_meta:                                              # Garage metadata directory (bind-mounted into container)
      path: "/zpool0/system/garage/meta"
      quota: "10G"
    shares:                                                          # file shares parent dataset
      path: "/zpool0/shares"
      quota: "5T"
    shares_media:                                                    # shared media (NFS + Samba)
      path: "/zpool0/shares/media"
      quota: "4T"
    shares_scanner_inbox:                                            # FTP upload root
      path: "/zpool0/shares/scanner-inbox"
      quota: "50G"
    iscsi:                                                           # zvol parent dataset for iSCSI LUNs
      path: "/zpool0/iscsi"
      quota: "500G"
    backups:                                                         # backup target dataset (snapshots/replication landing)
      path: "/zpool0/backups"
      quota: "2T"

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
      ports: [445]
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
      options: []                    # optional export-level default options; client-level options override these
      enabled: true                  # optional, default true

samba:
  global:
    workgroup: "WORKGROUP"
    server_string: "CloudyHome NAS" # optional
    min_protocol: "SMB3_11"         # minimum SMB protocol version; validated at boot. SMB1/NetBIOS not supported.
  shares:
    - name: "media"
      path: "/zpool0/shares/media"
      browsable: true
      read_only: false
      guest_ok: false
      users_ref: ["samba/users/smb_alice"]     # list of ref paths; each resolves to a user entry in samba.users map in secrets; maps to valid_users in smb.conf
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
          path: "iscsi/vmstore"           # zvol path (no pool prefix); apply script prepends storage.pool; block device: /dev/zvol/{pool}/{path}
          size: "100G"                   # zvol size; created if not present, left untouched if exists
          readonly: false
      auth:
        discovery_auth: "none"           # one of none|chap
        session_auth: "chap"             # one of none|chap
        chap_secret_ref: "iscsi/vmstore" # key in secrets file
      initiators:
        - "iqn.1993-08.org.debian:client1"  # empty list means no initiator is allowed (deny all)
      enabled: true                    # optional, default true

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
  users_ref: ["ftp/users/scanner1"]          # list of ref paths; each resolves to a user entry in ftp.users map in secrets; maps to USERS env
  upload_root: "/zpool0/shares/scanner-inbox"

health:
  alert:
    enabled: true                             # optional, default false; when false, smartd/ZED still log to journal but no email is sent
    smtp_host: "smtp.example.com"             # SMTP relay hostname
    smtp_port: 587                            # SMTP relay port (587 for STARTTLS, 465 for implicit TLS)
    smtp_tls: "starttls"                      # one of: starttls | tls | off
    smtp_auth_ref: "health/smtp_auth"         # key in secrets file; resolves to map with username and password
    addresses_ref: "health/addresses"         # key in secrets file; resolves to map with from_address and to_address
```

### 14.4 Validation Rules

**Global IP policy**: Every IP address or CIDR resolved anywhere in `services.yml` or `secrets.enc.yaml` must be a valid RFC1918 address (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`). This applies to `host_ip_ref`, all `sources_ref` lists, all `cidr_ref` lists, and any other IP value. Non-RFC1918 values fail validation regardless of where they appear.

**Global email domain policy**: When `health.alert.enabled=true`, every value that represents an email address anywhere in `secrets.enc.yaml` must have its domain part (everything after `@`) match one of the entries in the top-level `allowed_email_domains` list in secrets. Comparison is case-insensitive. This applies to `health.addresses.from_address`, `health.addresses.to_address`, and any other email address field. `health.smtp_auth.username` is exempt — it is an authentication credential, not an email address. Validation fails if any email address uses a domain not in the allowed list. `allowed_email_domains` must be a non-empty list of non-empty strings when `health.alert.enabled=true`; it may be absent or omitted when alerting is disabled or `health` is not configured.

- `disks.ids` in secrets must be a non-empty list of non-empty strings. Empty list or absent key is a fatal error. (Runtime disk presence verification — checking that `/dev/disk/by-id/<id>` exists for each entry — is performed by `cloudyhome-zfs-import.service` (Section 6.2), not by schema validation.)
- `version` must equal `1`.
- `host_ip_ref` is required and must resolve to a valid RFC1918 IP address in secrets.
- `firewall` is required; omitting it is a validation error.
- `firewall.default_input` must be one of `drop` or `accept`.
- `firewall.rules` must be a non-empty list.
- `firewall.rules[*].service` must be unique.
- Each rule must have `service` (non-empty string), at least one of `ports` or `port_range`, `proto` (non-empty list), and `sources_ref` (non-empty string).
- `ports` entries must be valid port numbers in the range 1001–65535, except for well-known ports required by their protocol: SSH (22), Samba SMB (445), FTP control (21).
- `port_range` must be a two-element list `[min, max]` where `min <= max` and both values are in the range 1001–65535.
- `proto` entries must be one of `tcp` or `udp`.
- `sources_ref` must resolve to a non-empty list of IPs or CIDRs in secrets (RFC1918 enforced by global IP policy). Bare IPv4 addresses without a prefix length (e.g. `"192.168.1.50"`) are accepted and treated as `/32`; the renderer must emit them with an explicit `/32` suffix in the nftables rule.
- A rule may not define both `ports` and `port_range`.
- **Firewall rules are literal**: the renderer emits nftables rules exactly as declared — no merging, deduplication, or cross-validation against service config ports. It is the operator's responsibility to keep firewall rules consistent with service ports. Port overlaps between rules (same port, same protocol) are not rejected; they produce separate nftables rules as written.
- `storage.pool` must be `zpool0` for this deployment.
- `storage.datasets` must be a non-empty map with unique keys and unique `path` values.
- Each key must be a non-empty underscore-separated identifier (simple name, e.g. `shares_media`).
- Each value must be a map with required keys `path` and `quota`.
- `path` must be an absolute mount path starting with `/zpool0/`. The ZFS dataset name is the map key (e.g. `shares_media`); the apply script constructs the full ZFS name as `{storage.pool}/{key}` for ZFS commands.
- `quota` must be a valid ZFS size string (integer followed by a unit suffix: `K`, `M`, `G`, or `T`, e.g. `"500G"`, `"2T"`).
- Any `path` intended for data export must start with `/zpool0/`.
- **Path-to-dataset cross-validation**: every service data path must match a `path` value in `storage.datasets` so the apply service auto-creates it. Checked paths: `nfs.exports[*].path`, `samba.shares[*].path`, `garage.data_dir`, `garage.metadata_dir`, `ftp.upload_root`. For iSCSI zvol paths (`iscsi.targets[*].luns[*].path`), the parent dataset key is derived by stripping the last component (e.g., zvol path `iscsi/vmstore` → parent key `iscsi`), which must exist as a key in `storage.datasets`. Unmatched paths fail validation.
- `nfs.version` must be `4`. NFSv3 is not supported in this deployment; any other value fails validation. This check is enforced in `cloudyhome-nas-validate.service` before the boot chain proceeds.
- `nfs.exports` must be a non-empty list when `nfs` is present.
- `nfs.exports[*].name` must be unique.
- `nfs.exports[*].path` must be unique.
- `nfs.exports[*].enabled` is optional; defaults to `true`. When `false`, the export is excluded from the rendered `/etc/exports.d/cloudyhome.exports` — the renderer skips it entirely. Validation of the export's fields still applies (the item must be fully specified), except where explicitly relaxed below.
- `nfs.exports[*].clients` must be non-empty when export is enabled. When `enabled=false`, clients may be empty.
- `nfs.exports[*].clients[*].cidr_ref` is required and must resolve to a non-empty CIDR list in secrets (RFC1918 enforced by global IP policy).
- `nfs.exports[*].options` is optional; defaults to `[]`. These are the default NFS options for the export. When a client block also specifies `options`, the client-level options replace the export-level defaults entirely (no merge).
- `nfs.exports[*].clients[*].options` is optional; defaults to `[]`. When present and non-empty, overrides the export-level `options` for that client block. When absent or empty, the export-level `options` apply.
- `nfs.exports[*].clients[*].identity_map.mode` is optional; defaults to `root_squash`.
- `nfs.exports[*].clients[*].identity_map.mode` must be one of:
  - `root_squash`
  - `no_root_squash`
  - `all_squash`
- If `identity_map.mode=all_squash`, both `anon_uid` and `anon_gid` are required.
- `nfs.exports[*].clients[*].options` and `nfs.exports[*].options` must not contain identity mapping directives (`root_squash`, `no_root_squash`, `all_squash`, or any option matching `anonuid=*` or `anongid=*`). These are handled exclusively by the `identity_map` structured field; including them in `options` would produce duplicate NFS export options.
- `samba.global.min_protocol` must be `"SMB3_11"`. SMB1, SMB2, and legacy NetBIOS are not supported in this deployment; any other value fails validation. This check is enforced in `cloudyhome-nas-validate.service` before the boot chain proceeds.
- `samba.shares` must be a non-empty list when `samba` is present.
- `samba.shares[*].enabled` is optional; defaults to `true`. When `false`, the share is excluded from the rendered `/etc/samba/smb.conf` — the renderer skips it entirely. Validation of the share's fields still applies (the item must be fully specified), except where explicitly relaxed below.
- `samba.shares[*].name` must be unique.
- `samba.users` in secrets must be a non-empty map with unique keys (each key is a username).
- `samba.shares[*].users_ref` must be a non-empty list of ref path strings when share is enabled. When `enabled=false`, `users_ref` may be empty. Each path must resolve to an entry in the `samba.users` map in secrets (e.g., `"samba/users/smb_alice"` resolves to the `smb_alice` key). Unresolvable paths fail closed.
- All Samba usernames (`samba.users` keys in secrets) must be prefixed with `smb_`. This ensures Samba system accounts are clearly namespaced and cannot collide with other OS users.
- `iscsi.targets` must be a non-empty list when `iscsi` is present.
- `iscsi.targets[*].enabled` is optional; defaults to `true`. When `false`, the target is excluded from the rendered `/etc/target/saveconfig.json` — the renderer skips it entirely. Validation of the target's fields still applies (the item must be fully specified), except where explicitly relaxed below.
- `iscsi.base_iqn` is required when `iscsi` is present and must be a non-empty string in valid IQN format (`iqn.YYYY-MM.<domain>:<string>`).
- `iscsi.portal_port` is required when `iscsi` is present and must be a valid port number in the range 1001–65535.
- `iscsi.targets[*].name` must be unique.
- `iscsi.targets[*].iqn_suffix` must be non-empty.
- `iscsi.targets[*].iqn_suffix` must be unique across all targets (duplicate suffixes produce duplicate IQNs).
- `iscsi.targets[*].luns` must be a non-empty list when target is enabled. When `enabled=false`, `luns` may be empty.
- `iscsi.targets[*].luns[*].lun` must be unique per target.
- `iscsi.targets[*].luns[*].path` must be unique across all targets.
- `iscsi.targets[*].initiators` defaults to deny-all when empty. The renderer must not allow implicit open access — an empty list is valid and means no initiator is permitted.
- `iscsi.targets[*].auth.discovery_auth` must be one of `none` or `chap`.
- `iscsi.targets[*].auth.session_auth` must be one of `none` or `chap`.
- If `session_auth=chap`, `chap_secret_ref` is required and must resolve to a map with two non-empty string fields: `chap_user` and `chap_password`.
- `iscsi.targets[*].luns[*].type` must be `"zvol"`. No other LUN types are supported in this deployment.
- `iscsi.targets[*].luns[*].size` is required for `type=zvol` and must be a valid ZFS size string (e.g. `"100G"`).
- `iscsi.targets[*].luns[*].path` omits the pool prefix (e.g. `iscsi/vmstore`); the apply script prepends `storage.pool` for both `zfs create -V` and `/dev/zvol/` derivation (block device: `/dev/zvol/{pool}/{path}`).
- `garage.enabled=true` requires:
  - `runtime=podman-quadlet-root`
  - non-empty `quadlet_name`
  - non-empty `image` (Garage container image is `dxflrs/garage`; version pinning is managed in the Quadlet file, not validated here)
  - both `admin_token_ref` and `rpc_secret_ref`
  - `admin_port` must be specified and must be a valid port number in the range 1001–65535; no firewall rule is generated for it — blocked by default-drop
  - `config_dir` must be a non-empty absolute path; the renderer writes `garage.toml` into this directory and the Quadlet mounts it read-only into the container
  - `data_dir` must start with `/zpool0/`
  - `metadata_dir` must start with `/zpool0/`
  - `s3_region` must be a non-empty string.
  - `layout_capacity` must be non-empty.
  - `rpc_port` must be a valid port number in the range 1001–65535.
  - `s3_port` must be a valid port number in the range 1001–65535.
  - `replication_mode` must be one of `"none"`, `"1"`, `"2"`, `"3"`.
- `ftp.enabled=true` requires:
  - `runtime=podman-quadlet-root`
  - non-empty `quadlet_name`
  - `config_dir` must be a non-empty absolute path; the renderer writes `ftp.env` into this directory and the Quadlet mounts it into the container
  - non-empty `image` (FTP container image is `delfer/alpine-ftp-server`; version pinning is managed in the Quadlet file, not validated here)
  - `control_port` must be `21`. Hardware scanners expect the standard FTP control port; non-standard values are not supported in this deployment.
  - valid passive range (`passive_ports.min <= passive_ports.max`)
  - `upload_root` under `/zpool0/`
  - `users_ref` must be a non-empty list of ref path strings
- `ftp.users` in secrets must be a non-empty map with unique keys (each key is a username).
- Each `ftp.users_ref` path must resolve to an entry in the `ftp.users` map in secrets (e.g., `"ftp/users/scanner1"` resolves to the `scanner1` key). Unresolvable paths fail closed.
- `health` is optional. If absent, alerting defaults to journal-only (smartd and ZED still run and log to the journal; no email notifications).
- `health.alert.enabled` is optional, defaults to `false`. When `false`, the alert script logs to the journal only.
- When `health.alert.enabled=true`, all of the following are required:
  - `smtp_host` — non-empty string.
  - `smtp_port` — valid port number in range 1–65535.
  - `smtp_tls` — must be one of `starttls`, `tls`, or `off`.
  - `smtp_auth_ref` — must resolve to a map in secrets with two non-empty string fields: `username` and `password`.
  - `addresses_ref` — must resolve to a map in secrets with two non-empty string fields: `from_address` and `to_address` (both must contain `@`).
- Email addresses are not fully validated at schema level (RFC5321 validation is impractical). The `@` check catches obvious misconfigurations; actual deliverability is verified during the first real alert.
- All email addresses are subject to the global email domain policy (see above).

### 14.5 Secrets Mapping Contract

`secrets.enc.yaml` contains both reference-path-keyed values (resolved via `_ref` fields in `services.yml`) and standalone top-level keys (`allowed_email_domains`, `disks.ids`) accessed directly by the validator and import scripts:

```yaml
allowed_email_domains:                           # required when health.alert.enabled=true; may be omitted otherwise
  - "example.com"

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
    smb_alice:                                    # key is the username; must be prefixed with smb_
      password: "REDACTED"
# Note: The apply service creates the corresponding Unix system account before provisioning into tdbsam.
ftp:
  users:
    scanner1:                                     # key is the username
      password: "REDACTED"
health:
  smtp_auth:
    username: "nas@example.com"
    password: "REDACTED"
  addresses:
    from_address: "nas@example.com"
    to_address: "admin@example.com"
```

Note: `delfer/alpine-ftp-server` accepts `user|pass|uid|gid|homedir` but all fields after `password` are optional. This deployment uses only username and password. The renderer resolves each `users_ref` path to an entry in the `ftp.users` map and constructs the `USERS` env var as `user1|pass1:user2|pass2:...`.

Rules:
- References are resolved as slash-delimited paths (example: `garage/admin_token`).
- Renderer must fail if a referenced key is absent.
- Renderer must not print resolved secret values in logs.

Samba user mapping:
- Each share's `users_ref` is a list of ref paths (e.g., `["samba/users/smb_alice"]`). Each path resolves to an entry in the `samba.users` map in secrets. The renderer extracts the username from the resolved key and renders them as `valid_users` in `smb.conf`.
- Different shares may reference different subsets of users for per-share access control.
- User provisioning (OS accounts and `tdbsam`) is handled in `cloudyhome-nas-apply.service` by iterating the `samba.users` map.

---

## 15. Monitoring and Storage Health

### 15.1 Overview

Three subsystems provide storage health automation:

1. **ZFS scrub** — periodic scrub via systemd timer to detect silent data corruption.
2. **SMART monitoring** — `smartd` runs scheduled self-tests and monitors disk health attributes.
3. **ZFS Event Daemon (ZED)** — reacts to pool state changes, scrub results, and I/O errors in real time.

All three feed into a single alert script (`/usr/local/sbin/nas-health-alert`) that logs to the systemd journal and delivers email notifications via `msmtp`. The alert script config is rendered at boot by `cloudyhome-nas-render.service` to inject the SMTP endpoint and email addresses from secrets.

### 15.2 ZFS Scrub Schedule

Periodic ZFS scrub via `cloudyhome-zfs-scrub.service` and `cloudyhome-zfs-scrub.timer` (systemd design in Section 6.7 and 6.8).

Scrub results are reported by ZED (Section 15.4) on completion — no additional polling needed.

### 15.3 SMART Test Schedules

**Package**: `smartmontools` (installed in Packer image).

**Configuration**: static `/etc/smartd.conf` baked into the Packer image. Not rendered at boot — the VM only has the 6 passed-through SATA disks, so `DEVICESCAN` safely covers all of them without needing disk IDs from secrets.

```
DEVICESCAN -a -o on -S on -s (S/../.././02|L/../../6/03) -W 4,45,55 -m root -M exec /usr/local/sbin/nas-health-alert
```

Breakdown:
- `-a` — monitor all SMART attributes.
- `-o on` — enable automatic offline testing.
- `-S on` — enable automatic attribute autosave.
- `-s (S/../.././02|L/../../6/03)` — short self-test daily at 02:00; long self-test Saturdays at 03:00.
- `-W 4,45,55` — temperature monitoring: log if delta exceeds 4°C between checks, warn at 45°C, critical at 55°C.
- `-m root` — required by smartd syntax but delivery is handled by the exec script, not mail.
- `-M exec /usr/local/sbin/nas-health-alert` — call the alert script instead of sending email directly.

`smartd.service` is enabled by the deliverables post-install script and started by the rendered `nas-apply-services.sh` script on every boot (Section 6.9).

### 15.4 ZFS Event Daemon (ZED)

**Package**: `zfs-zed` (installed in Packer image; included with `zfsutils-linux` on Debian).

ZED watches the ZFS kernel event stream and fires shell scripts (ZEDLETs) on events. The relevant stock ZEDLETs and their behavior:

| Event class | ZEDLET | Fires when |
|---|---|---|
| `scrub_finish` | `scrub_finish-notify.sh` | Scrub completes (includes error counts) |
| `statechange` | `statechange-notify.sh` | Pool transitions to DEGRADED, FAULTED, etc. |
| `io_error` | `io-notify.sh` | Checksum, read, or write errors exceed threshold |
| `resilver_finish` | `resilver_finish-notify.sh` | Resilver completes after disk replacement |

**ZED configuration** (`/etc/zfs/zed.d/zed.rc`): baked into the Packer image with these settings:

```sh
ZED_NOTIFY_VERBOSE=1
ZED_NOTIFY_DATA=1
ZED_SYSLOG_TAG="zed"
ZED_SYSLOG_SUBCLASS_INCLUDE="scrub_finish,statechange,io_error,resilver_finish"

# Disable built-in email — alert delivery handled by the alert script
ZED_EMAIL_ADDR=""
ZED_EMAIL_PROG=""

# Disable unused built-in notification methods
ZED_PUSHBULLET_ACCESS_TOKEN=""
ZED_NTFY_URL=""
```

**Custom ZEDLET**: a minimal wrapper script baked into the image that calls `/usr/local/sbin/nas-health-alert`, inheriting the ZED environment variables (`ZEVENT_CLASS`, `ZEVENT_POOL`, `ZEVENT_VDEV_STATE`, etc.) so the alert script can read them directly. ZED matches scripts by filename prefix to event subclass, so one symlink per event class is created in `/etc/zfs/zed.d/` by the installer Makefile, all pointing to the same script:
  - `statechange-nas-health-alert.sh`
  - `scrub_finish-nas-health-alert.sh`
  - `io-nas-health-alert.sh`
  - `resilver_finish-nas-health-alert.sh`

The target script is placed at `/usr/local/sbin/nas-zedlet-wrapper` (outside `/etc/zfs/zed.d/` to avoid being executed directly by ZED as an `all-` script).

`zfs-zed.service` is enabled at runtime by `nas-apply-config` and started by the rendered `nas-apply-services.sh` script on every boot (Section 6.10).

### 15.5 Alert Delivery

The alert script always logs to the systemd journal first. If email alerting is enabled (`ALERT_ENABLED=true` in `alert.conf`), it then delivers via email. Journal logging is unconditional; email delivery depends on configuration.

**Journal** (always active):
- Alert script writes a structured log entry to the systemd journal via `logger` with priority and identifier tags.
- No external dependencies. External monitoring stacks (Prometheus + Alertmanager, Loki, etc.) can scrape the journal independently.
- This step runs unconditionally before email delivery — if email fails, the alert is still in the journal.

**Email** (primary notification):
- Sends via `msmtp` (lightweight SMTP relay client). No local MTA — `msmtp` connects directly to an upstream SMTP relay.
- Subject line includes severity, hostname, and source (e.g., `[CRITICAL] nas01: ZFS pool DEGRADED`).
- Body includes timestamp, source, severity, event details, and (for ZED events) pool/vdev state.
- Timeout: 30 seconds. Delivery failures are logged to the journal but do not block the caller or suppress future alerts.
- `msmtp` is configured via `/etc/msmtprc`, rendered at boot by `cloudyhome-nas-render.service`.

The email configuration and rendered `msmtprc` + `alert.conf` are read by the alert script at invocation time (not cached at boot).

### 15.6 Alert Script

**Path**: `/usr/local/sbin/nas-health-alert`

**Language**: Shell (POSIX). The script is called by both `smartd` (`-M exec`) and ZED (ZEDLET wrapper). Both callers pass information via environment variables, not arguments.

**Behavior**:
1. Read `/etc/cloudyhome/health/alert.conf` for alert settings (`ALERT_ENABLED`, `ALERT_TO`, `ALERT_FROM`).
2. Determine source and severity from caller environment:
   - **smartd**: `SMARTD_DEVICE`, `SMARTD_FAILTYPE`, `SMARTD_MESSAGE` are set. Severity mapping: `SMARTD_FAILTYPE=EmailTest` → `info`; `SMARTD_FAILTYPE=Health` → `critical`; all others → `warning`.
   - **ZED**: `ZEVENT_CLASS`, `ZEVENT_POOL`, `ZEVENT_SUBCLASS` are set. Severity: `statechange` to DEGRADED/FAULTED → `critical`; `io_error` → `warning`; `scrub_finish` with errors → `warning`, without errors → `info`; `resilver_finish` → `info`.
3. Construct message string with hostname, timestamp, source, severity, and event details.
4. Always log to journal via `logger -t nas-health-alert -p <priority>`.
5. If `ALERT_ENABLED=true`: send email via `msmtp` using rendered `/etc/msmtprc` config, with `ALERT_TO` as recipient and `ALERT_FROM` as sender. Log delivery success/failure to journal. Do not retry on failure. If `ALERT_ENABLED=false` or `alert.conf` is missing: skip email, journal entry is the only output.

**Permissions**: `0755 root:root`. The script contains no secrets — it reads the rendered config file at runtime.

### 15.7 Render and Validation Integration

The `health` schema, validation rules, and secrets are defined in the canonical locations: Section 14.3 (field contract), Section 14.4 (validation rules), and Section 14.5 (secrets mapping).

**Render targets**:

1. `/etc/cloudyhome/health/alert.conf` — shell-sourceable file read by the alert script:
   ```
   ALERT_ENABLED=true
   ALERT_TO=admin@example.com
   ALERT_FROM=nas@example.com
   ```
   - Permissions: `0600 root:root` (addresses resolved from secrets via `addresses_ref`).
   - If `health` is absent or `alert.enabled=false`, rendered with `ALERT_ENABLED=false` and no other fields.

2. `/etc/msmtprc` — msmtp configuration file:
   ```
   defaults
   auth           on
   tls            on
   tls_starttls   on
   syslog         LOG_MAIL

   account        default
   host           smtp.example.com
   port           587
   from           nas@example.com
   user           nas@example.com
   password       REDACTED
   ```
   - Permissions: `0600 root:root` (contains SMTP password).
   - Only rendered when `health.alert.enabled=true`. If alerting is disabled, `/etc/msmtprc` is not rendered (msmtp is unused).
   - `tls` and `tls_starttls` fields are derived from `smtp_tls`: `starttls` → `tls on` + `tls_starttls on`; `tls` → `tls on` + `tls_starttls off`; `off` → `tls off` + `tls_starttls off`.

**Validation**: `nas-validate-config` validates the `health` section if present. No runtime validator needed for `alert.conf` or `msmtprc` — both are generated from Pydantic-validated input.

**Render ordering**: no special ordering. Both files are rendered in the same pass as all other config files. The alert script reads them at event time, not at boot.

### 15.8 Packer Image Requirements

Added to `PACKER_CHECKLIST.md` scope:

- **Packages**: `smartmontools`, `zfs-zed` (if not already pulled in by `zfsutils-linux`), `msmtp` (lightweight SMTP client).
- **Baked files** (all under `nas_root/`, copied by Packer):
  - `/etc/smartd.conf` — static config per Section 15.3.
  - `/etc/zfs/zed.d/zed.rc` — static config per Section 15.4.
  - `/usr/local/sbin/nas-zedlet-wrapper` — ZEDLET wrapper script.
  - `/usr/local/sbin/nas-health-alert` — alert delivery script.
  - `cloudyhome-zfs-scrub.service` and `cloudyhome-zfs-scrub.timer` — installed to `/etc/systemd/system/`.
- **Post-copy setup**: handled entirely by `make -C /var/lib/cloudyhome/installer install`. This installs the `cloudyhome` Python package, sets script permissions, runs `systemctl enable` for all custom units (including the scrub timer), masks/disables stock services, and creates ZEDLET symlinks in `/etc/zfs/zed.d/`. Stock services (`smartd.service`, `zfs-zed.service`) are enabled at runtime by `nas-apply-config`.
- **Templates**: Jinja2 templates for `alert.conf` and `msmtprc` added to the render script's template set.

### 15.9 Open Decisions

- **Scrub frequency tuning**: The 1st/15th schedule is a starting point. May need adjustment based on pool size and observed scrub duration. Scrub duration can be checked post-deployment and the timer adjusted in a future image build.
- **SMART long test overlap**: Long tests on Saturday at 03:00 may overlap with a scrub if the 1st/15th falls on a Saturday. ZFS scrub and SMART long tests can coexist — scrub operates at the ZFS layer while SMART tests run at the disk firmware layer — but combined I/O load may be noticeable. Acceptable for a home NAS; revisit if performance-sensitive workloads run during that window.
- **Alert escalation**: The current design has no escalation (repeat alerts, paging). A single email per event is delivered. If escalation is needed, it should be handled downstream (e.g., email rules that forward to a paging service, or replacing msmtp delivery with a webhook to an alerting platform).
- **Health check endpoint**: No HTTP health endpoint is exposed by this design. If external monitoring (Prometheus node_exporter, etc.) is added later, ZFS and SMART metrics can be exported via collectors rather than the alert script. This is a separate concern from email alerting.
- **SMTP relay dependency**: Email delivery depends on an external SMTP relay being reachable. If the relay is down, alerts are lost (but still present in the journal). No local mail queue or retry is implemented — msmtp is a fire-and-forget relay client.

---

## 16. Source Tree Layout

The project deliverables live under `nas_root/`. The tree mirrors the target filesystem so Packer can copy it directly into the image. After copying, Packer runs `make -C /var/lib/cloudyhome/installer install` — that single step handles all post-copy setup.

```
nas_root/
├── usr/
│   └── local/
│       ├── lib/
│       │   └── cloudyhome/                  # Python package (installed by make install)
│       │       ├── pyproject.toml
│       │       └── cloudyhome/
│       │           ├── __init__.py
│       │           ├── models.py
│       │           ├── render.py
│       │           ├── secrets.py
│       │           └── validate.py
│       └── sbin/
│           ├── nas-validate-config          # Python — schema validation
│           ├── nas-zfs-import               # Shell — disk check + pool import
│           ├── nas-render-config            # Python — Jinja2 renderer
│           ├── nas-apply-config             # Python — datasets/zvols, users, service lifecycle
│           ├── nas-garage-bootstrap         # Python — Garage layout init
│           ├── nas-health-alert             # Shell — journal + email alerting
│           └── nas-zedlet-wrapper           # Shell — ZED → alert bridge
├── etc/
│   ├── systemd/
│   │   └── system/
│   │       ├── cloudyhome-nas-validate.service
│   │       ├── cloudyhome-zfs-import.service
│   │       ├── cloudyhome-nas-render.service
│   │       ├── cloudyhome-nas-firewall.service
│   │       ├── cloudyhome-nas-apply.service
│   │       ├── cloudyhome-garage-bootstrap.service
│   │       ├── cloudyhome-zfs-scrub.service
│   │       └── cloudyhome-zfs-scrub.timer
│   ├── smartd.conf                          # static — SMART test schedule (Section 15.3)
│   ├── zfs/
│   │   └── zed.d/
│   │       └── zed.rc                       # static — ZED config (Section 15.4)
│   └── cloudyhome/
│       └── templates/                       # Jinja2 templates for rendered configs
│           ├── nftables.conf.j2
│           ├── exports.j2
│           ├── smb.conf.j2
│           ├── garage.toml.j2
│           ├── ftp.env.j2
│           ├── cloudyhome-garage.container.j2
│           ├── cloudyhome-ftp.container.j2
│           ├── alert.conf.j2
│           ├── msmtprc.j2
│           └── nas-apply-services.sh.j2
└── var/
    └── lib/
        └── cloudyhome/
            ├── installer/
            │   └── Makefile                 # make install: pip, chmod, systemctl enable, symlinks
            └── nas/
                ├── services.yml             # canonical config (real values at deploy time)
                └── secrets.enc.yaml         # SOPS-encrypted secrets
```

Notes:
- No symlinks live in the source tree. The installer Makefile creates them all: `systemctl enable` for custom cloudyhome units, `ln -sf` for ZEDLET handlers in `/etc/zfs/zed.d/`.
- `cloudyhome-garage-bootstrap.service` is intentionally not enabled — it has no `WantedBy=` and is triggered exclusively by the rendered `nas-apply-services.sh`.
- ZEDLET symlinks use absolute targets (`/usr/local/sbin/nas-zedlet-wrapper`). The wrapper lives outside `zed.d/` to avoid being executed directly by ZED as an `all-` script.
- All scripts under `usr/local/sbin/` are set to `0755 root:root` by the installer Makefile.
- The `cloudyhome` Python package is installed system-wide via `pip install --break-system-packages` by the installer Makefile. Dependencies (`pydantic`, `pyyaml`, `jinja2`) are declared in `pyproject.toml` and installed automatically.
