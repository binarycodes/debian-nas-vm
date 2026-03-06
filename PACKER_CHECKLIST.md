# Packer Image Build Checklist

Items that must be handled during Packer image build and are out of scope for the runtime boot chain.

## Install packages and tools

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
```

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

## Pre-pull container images

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

## Disable NFS and Samba auto-start (best-effort)

Disable the default auto-start of `nfs-server.service` and `smbd.service` in the Packer image so they do not start before the cloudyhome boot chain has rendered and applied config:

```
systemctl disable nfs-server smbd
```

**Note**: The runtime boot chain (`cloudyhome-nas-apply.service`) uses `reload-or-restart` and handles both cases — services already running or stopped. This Packer step is best-effort hygiene only; the system is correct either way.

## AGE private key cloud-init permissions (mandatory)

The cloud-init `write_files` entry that delivers the AGE private key to `/etc/sops/age/keys.txt` must explicitly set:

```yaml
permissions: '0600'
owner: root:root
```

Failure to set these means the AGE key could be world-readable at first boot. This must be verified in the cloud-init config before any image is built.
