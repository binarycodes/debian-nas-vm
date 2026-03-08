# Packer Image Build Checklist

Packer has three responsibilities:

1. Copy `nas_root/` into the image root.
2. Run `make -C /var/lib/cloudyhome/installer install` — this installs all packages, pre-pulls container images, installs the Python library, sets permissions, enables services, and creates symlinks. `sops` and `age` are assumed to be present in the base golden image and are not installed by this step.
3. Inject the real `services.yml` and `secrets.enc.yaml` (see below).

## 1. Inject `services.yml` and `secrets.enc.yaml` (mandatory)

The source tree contains placeholder versions of both files. They **must** be replaced with real versions before the image is usable.

**`services.yml`** — the real NAS configuration for this deployment:
- **Target path**: `/var/lib/cloudyhome/nas/services.yml`

**`secrets.enc.yaml`** — encrypt your real secrets with `sops --encrypt --age <pubkey> secrets.yaml > secrets.enc.yaml` before injecting:
- **Target path**: `/var/lib/cloudyhome/nas/secrets.enc.yaml`

Packer provisioner example:
```
# file provisioner
source = "path/to/services.yml"
destination = "/var/lib/cloudyhome/nas/services.yml"

source = "path/to/secrets.enc.yaml"
destination = "/var/lib/cloudyhome/nas/secrets.enc.yaml"
```

`secrets.enc.yaml` permissions are set to `0600 root:root` by `make install`. If injecting after `make install` has already run, set them explicitly:
```
chmod 0600 /var/lib/cloudyhome/nas/secrets.enc.yaml
chown root:root /var/lib/cloudyhome/nas/secrets.enc.yaml
```

The image will fail to boot correctly if either file is absent or contains placeholder data.

## 2. AGE private key cloud-init permissions (mandatory)

The cloud-init `write_files` entry that delivers the AGE private key to `/etc/sops/age/keys.txt` must explicitly set:

```yaml
permissions: '0600'
owner: root:root
```

Failure to set these means the AGE key could be world-readable at first boot. This must be verified in the cloud-init config before any image is built.
