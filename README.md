# CloudyHome NAS

A declarative configuration manager for a home NAS server. Define your storage, network shares, firewall rules, and services in a single YAML file, and CloudyHome renders validated configs and applies them at boot via systemd.

## What It Manages

- **ZFS** datasets (pools, quotas, compression)
- **NFS** exports
- **Samba** shares
- **iSCSI** targets (via targetcli)
- **nftables** firewall
- **Garage** S3-compatible object storage (Podman/Quadlet)
- **FTP** server (Podman/Quadlet)
- **Health alerts** via SMTP

## How It Works

1. You describe your NAS in `services.yml` and store secrets in `secrets.enc.yaml`.
2. At boot, a systemd chain validates the config, renders Jinja2 templates into system config files (nftables, exports, smb.conf, etc.), and applies them.
3. Each rendered file is validated before being atomically written (e.g. `nft -c`, `testparm`), so a bad config never replaces a working one.

## Installation

Build and install the Debian package:

```bash
./packaging/build.sh
sudo dpkg -i cloudyhome-nas_0.1.0_all.deb
```

### Dependencies

See `packaging/DEBIAN/control` for the full list.

## Configuration

Edit the two files under `/var/lib/cloudyhome/nas/`:

- **`services.yml`** — declarative definition of storage, firewall, shares, and services.
- **`secrets.enc.yaml`** — encrypted secrets (Samba passwords, iSCSI CHAP keys, Garage tokens, etc.). References in `services.yml` use `_ref` suffixes to point into this file.

After editing, rerun the render/apply chain:

```bash
sudo systemctl start cloudyhome-nas-ready.target
```

