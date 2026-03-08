# Packer Image Build Checklist

Items that must be handled during Packer image build and are out of scope for the runtime boot chain. The project deliverables (scripts, units, templates, config files) are baked into the image by Packer from the source tree; this checklist covers the prerequisites and image-level configuration that the mvp1.md boot chain depends on.

**Important**: The `nas_root/` source tree contains symlinks (systemd `WantedBy` symlinks, ZEDLET symlinks) with relative or absolute targets that may not resolve on the build machine. Packer must copy the tree using a symlink-preserving method (`cp -a`, `rsync -a`, or Packer's `file` provisioner). Do **not** dereference symlinks during copy.

## 1. Install packages and tools

All packages below must be installed in the Packer image.

System packages:
```
zfsutils-linux
nfs-kernel-server
samba
targetcli-fb
podman
cloud-init
nftables
yq
smartmontools
msmtp
```

Note: `zfs-zed` is required but is typically included with `zfsutils-linux` on Debian. Verify it is present after install; if not, install explicitly.

Secrets tooling (install from upstream releases or distro packages):
```
sops
age
```

Python runtime and libraries:
```
python3
python3-pip
```

Python packages (via pip):
```
pyyaml
pydantic
jinja2
tomli-w
```

## 2. Pre-pull container images

Container images must be pulled into the Podman image store during Packer build so that Quadlet services can start at boot without any network dependency.

Images to pre-pull:
- `dxflrs/garage:latest` (Garage S3) — **minimum Garage v1.x required**. The bootstrap script passes `layout_capacity` as a human-readable string (e.g. `"1G"`) to `garage layout assign -c`. This format is only supported in Garage v1.x and later; older versions require capacity as an integer in bytes.
- `delfer/alpine-ftp-server:latest` (FTP)

Pull command during Packer provisioning:
```
podman pull dxflrs/garage:latest
podman pull delfer/alpine-ftp-server:latest
```

The image store is persisted in the golden image. At boot, Podman finds the images locally and starts containers immediately.

## 3. Mask stock ZFS services

The cloudyhome boot chain manages ZFS import exclusively via `cloudyhome-zfs-import.service` (Section 6.2). Stock ZFS services must be masked to prevent conflicts:

```
systemctl mask zfs-import-cache.service zfs-import-scan.service zfs-mount.service zfs-share.service
```

## 4. Disable NFS, Samba, and iSCSI auto-start

Disable the default auto-start of `nfs-server.service`, `smbd.service`, and `target.service` so they do not start before the cloudyhome boot chain has rendered and applied config:

```
systemctl disable nfs-server smbd target
```

**Note**: The runtime boot chain (`cloudyhome-nas-apply.service`) uses `reload-or-restart` (NFS, Samba) and `restart` (iSCSI) and handles both cases — services already running or stopped. This step is best-effort hygiene only; the system is correct either way.

## 5. Service enabling (handled by deliverables, not Packer)

Packer does **not** enable any NAS-related services. All service enabling is handled by the project deliverables source tree:

- **Custom cloudyhome units** (`cloudyhome-nas-validate.service`, `cloudyhome-zfs-import.service`, `cloudyhome-nas-render.service`, `cloudyhome-nas-firewall.service`, `cloudyhome-nas-apply.service`, `cloudyhome-zfs-scrub.timer`): enabled by `WantedBy` symlinks included in the source tree (e.g. `multi-user.target.wants/cloudyhome-nas-validate.service → ../cloudyhome-nas-validate.service`). Packer copies these symlinks into place alongside the unit files — no `systemctl enable` required.
- **Stock services** (`smartd.service`, `zfs-zed.service`): enabled at runtime by `nas-apply-config` during the apply phase. Packer does not enable these — they should only run after the NAS boot chain has imported the pool and rendered config.
- `cloudyhome-garage-bootstrap.service` is intentionally NOT enabled — it has no `WantedBy=` and is driven exclusively by the rendered `nas-apply-services.sh` script.

Packer only enables generic infrastructure services (networking, SSH, NTP, etc.).

## 6. ZEDLET symlinks

ZEDLET symlinks are included in the `nas_root/` source tree (see mvp1.md Section 16) and copied into the image alongside all other deliverables. No manual `ln -s` commands are needed.

The source tree contains symlinks with absolute targets (e.g., `statechange-nas-health-alert.sh → /usr/local/sbin/nas-zedlet-wrapper`). These targets do not resolve on the build machine — that is expected. Packer must copy the source tree with a symlink-preserving method (`cp -a`, `rsync -a`, or Packer's `file` provisioner which preserves symlinks by default). Do **not** use a copy method that follows/dereferences symlinks.

The target script (`nas-zedlet-wrapper`) is placed outside `/etc/zfs/zed.d/` to avoid being executed directly by ZED as an `all-` script.

## 7. Script permissions

All scripts installed to `/usr/local/sbin/` must be `0755 root:root`:

```
chmod 0755 /usr/local/sbin/nas-validate-config
chmod 0755 /usr/local/sbin/nas-zfs-import
chmod 0755 /usr/local/sbin/nas-render-config
chmod 0755 /usr/local/sbin/nas-apply-config
chmod 0755 /usr/local/sbin/nas-garage-bootstrap
chmod 0755 /usr/local/sbin/nas-health-alert
chmod 0755 /usr/local/sbin/nas-zedlet-wrapper
```

## 8. Static config files

These are baked into the image as-is (not rendered at boot):

| File | Source | Notes |
|------|--------|-------|
| `/etc/smartd.conf` | Section 15.3 | SMART test schedule and alert exec |
| `/etc/zfs/zed.d/zed.rc` | Section 15.4 | ZED notification settings; disables built-in email |

## 9. Inject `secrets.enc.yaml` (mandatory)

The source tree contains `secrets.enc.yaml` as a plaintext structural reference. Before baking into the image it **must** be replaced with a SOPS-encrypted version:

- **Target path in image**: `/var/lib/cloudyhome/nas/secrets.enc.yaml`
- **Source**: encrypt your real secrets file with `sops --encrypt --age <pubkey> secrets.yaml > secrets.enc.yaml` and copy it into the image in place of the source-tree placeholder.

Packer provisioner example (file provisioner or shell):
```
# file provisioner
source = "path/to/secrets.enc.yaml"
destination = "/var/lib/cloudyhome/nas/secrets.enc.yaml"
```

Permissions must be `0600 root:root`:
```
chmod 0600 /var/lib/cloudyhome/nas/secrets.enc.yaml
chown root:root /var/lib/cloudyhome/nas/secrets.enc.yaml
```

The image will fail to boot correctly if this file is absent — `nas-render-config` calls SOPS to decrypt it at boot and will exit with an error if the file is missing.

## 10. AGE private key cloud-init permissions (mandatory)

The cloud-init `write_files` entry that delivers the AGE private key to `/etc/sops/age/keys.txt` must explicitly set:

```yaml
permissions: '0600'
owner: root:root
```

Failure to set these means the AGE key could be world-readable at first boot. This must be verified in the cloud-init config before any image is built.
