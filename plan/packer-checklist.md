# Packer Image Build Checklist

Packer has four responsibilities, in this order:

1. Copy `nas_root/` into the image root.
2. Inject the real `services.yml` (see section 1 below). **Must happen before `make install`** because the install phase runs `nas-validate-install-phase`, which validates `services.yml` schema and static cross-field rules. If `services.yml` is invalid or still contains placeholder data, the build will fail here.
3. Run `make -C /var/lib/cloudyhome/installer install` — installs all packages, pre-pulls container images, installs the Python library, sets permissions, validates `services.yml`, enables services, and creates symlinks. `sops` and `age` are assumed to be present in the base golden image and are not installed by this step.
4. Inject the real `secrets.enc.yaml` (see section 1 below). Injected after `make install` because secrets cannot be decrypted at build time (no AGE key present). Permissions are set by step 3; if re-injecting after install, set them explicitly.

## 1. Inject `services.yml` and `secrets.enc.yaml` (mandatory)

The source tree contains placeholder versions of both files. They **must** be replaced with real versions before the image is usable.

**`services.yml`** — inject **before** `make install`:
- **Target path**: `/var/lib/cloudyhome/nas/services.yml`
- `make install` reads `services.yml` for two reasons:
  1. **Image pull** — `pull-images` reads `.garage.image` and `.ftp.image` from `services.yml` to know which container images to pre-pull. A missing or placeholder file means the wrong images (or no images) get pulled.
  2. **Install-phase validation** — `nas-validate-install-phase` validates the schema and static cross-field rules (path-to-dataset consistency, pool name alignment, etc.). A missing or placeholder `services.yml` will abort the build at this step.
- Secret-dependent checks (host IP, disk IDs, firewall sources, CHAP, tokens) are **not** run at install time — only schema and secrets-free structural checks.

**`secrets.enc.yaml`** — inject **after** `make install`:
- **Target path**: `/var/lib/cloudyhome/nas/secrets.enc.yaml`
- Encrypt with `sops --encrypt --age <pubkey> secrets.yaml > secrets.enc.yaml` before injecting.
- Cannot be validated at build time (no AGE private key present in the image during Packer build). Full secrets validation runs at first boot via `cloudyhome-nas-validate.service`.
- Permissions are set to `0600 root:root` by `make install`. If injecting after install, set them explicitly:
```
chmod 0600 /var/lib/cloudyhome/nas/secrets.enc.yaml
chown root:root /var/lib/cloudyhome/nas/secrets.enc.yaml
```

Packer provisioner order:
```
# 1. file provisioner — before make install
source = "path/to/services.yml"
destination = "/var/lib/cloudyhome/nas/services.yml"

# 2. shell provisioner
make -C /var/lib/cloudyhome/installer install

# 3. file provisioner — after make install
source = "path/to/secrets.enc.yaml"
destination = "/var/lib/cloudyhome/nas/secrets.enc.yaml"
```

The image will fail to boot correctly if either file is absent or contains placeholder data.

## 2. AGE private key cloud-init permissions (mandatory)

The cloud-init `write_files` entry that delivers the AGE private key to `/etc/sops/age/keys.txt` must explicitly set:

```yaml
permissions: '0600'
owner: root:root
```

Failure to set these means the AGE key could be world-readable at first boot. This must be verified in the cloud-init config before any image is built.
