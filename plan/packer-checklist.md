# Packer Image Build Checklist

Packer has three responsibilities, in this order:

1. Inject both `services.yml` and `secrets.enc.yaml` (see section 1 below). Both **must be present before the deb install**: `services.yml` because the install phase runs `nas-validate-install-phase` and pulls container images; `secrets.enc.yaml` because `make install` sets its permissions and it must exist at that point.
2. Copy `cloudyhome-nas.deb` to the image and install it with `apt-get install -y ./cloudyhome-nas.deb`. apt satisfies `Depends:` first (all required packages), then dpkg unpacks the files, then postinst runs `make -C /var/lib/cloudyhome/installer install` — pre-pulls container images, installs the Python library, sets permissions, validates `services.yml`, enables services, and creates symlinks. `sops` and `age` are assumed to be present in the base golden image and are not installed by this step.

## 1. Inject `services.yml` and `secrets.enc.yaml` (mandatory)

The source tree contains placeholder versions of both files. They **must** be replaced with real versions before the image is usable.

Both files must be injected **before** the deb install.

**`services.yml`**:
- **Target path**: `/var/lib/cloudyhome/nas/services.yml`
- `make install` reads `services.yml` for two reasons:
  1. **Image pull** — `pull-images` reads `.garage.image` and `.ftp.image` from `services.yml` to know which container images to pre-pull. A missing or placeholder file means the wrong images (or no images) get pulled.
  2. **Install-phase validation** — `nas-validate-install-phase` validates the schema and static cross-field rules (path-to-dataset consistency, pool name alignment, etc.). A missing or placeholder `services.yml` will abort the build at this step.
- Secret-dependent checks (host IP, disk IDs, firewall sources, CHAP, tokens) are **not** run at install time — only schema and secrets-free structural checks.

**`secrets.enc.yaml`**:
- **Target path**: `/var/lib/cloudyhome/nas/secrets.enc.yaml`
- Encrypt with `sops --encrypt --age <pubkey> secrets.yaml > secrets.enc.yaml` before injecting.
- Cannot be validated at build time (no AGE private key present in the image during Packer build). Full secrets validation runs at first boot via `cloudyhome-nas-validate.service`.
- `make install` sets permissions to `0600 root:root`.

Packer provisioner order:
```
# 1. file provisioners — both before deb install
source = "path/to/services.yml"
destination = "/var/lib/cloudyhome/nas/services.yml"

source = "path/to/secrets.enc.yaml"
destination = "/var/lib/cloudyhome/nas/secrets.enc.yaml"

# 2. file + shell provisioner — copy and install the deb
source = "cloudyhome-nas.deb"
destination = "/tmp/cloudyhome-nas.deb"
# then:
apt-get install -y /tmp/cloudyhome-nas.deb
```

The image will fail to boot correctly if either file is absent or contains placeholder data.

## 2. AGE private key cloud-init permissions (mandatory)

The cloud-init `write_files` entry that delivers the AGE private key to `/etc/sops/age/keys.txt` must explicitly set:

```yaml
permissions: '0600'
owner: root:root
```

Failure to set these means the AGE key could be world-readable at first boot. This must be verified in the cloud-init config before any image is built.
