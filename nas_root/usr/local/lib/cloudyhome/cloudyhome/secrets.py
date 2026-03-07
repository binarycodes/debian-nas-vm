"""Secret decryption and resolution utilities."""
import json
import logging
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

SECRETS_PATH = "/var/lib/cloudyhome/nas/secrets.enc.yaml"
RUN_DIR = "/run/nas"


def check_run_tmpfs():
    """Verify /run is mounted as tmpfs."""
    with open("/proc/mounts") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "/run" and parts[2] == "tmpfs":
                return True
    raise RuntimeError("/run is not mounted as tmpfs - refusing to decrypt secrets")


def decrypt_secrets(secrets_path=SECRETS_PATH, run_dir=RUN_DIR):
    """Decrypt SOPS-encrypted secrets file, return (data_dict, temp_file_path)."""
    check_run_tmpfs()
    os.makedirs(run_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=run_dir, prefix="secrets.", suffix=".yaml")
    os.close(fd)
    try:
        subprocess.run(
            ["sops", "-d", "--output", tmp_path, secrets_path],
            check=True,
            capture_output=True,
            text=True,
        )
        import yaml
        with open(tmp_path) as f:
            data = yaml.safe_load(f)
        return data, tmp_path
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


@contextmanager
def secrets_context(secrets_path=SECRETS_PATH, run_dir=RUN_DIR):
    """Context manager that decrypts secrets and guarantees cleanup."""
    data, tmp_path = decrypt_secrets(secrets_path, run_dir)
    try:
        yield data
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
            log.debug("Cleaned up decrypted secrets")


def resolve_ref(secrets, path):
    """Resolve a slash-delimited reference path in the secrets dict.

    Example: resolve_ref(secrets, "garage/admin_token") returns secrets["garage"]["admin_token"]
    """
    parts = path.split("/")
    current = secrets
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Secret ref '{path}' not found (failed at '{part}')")
        current = current[part]
    return current
