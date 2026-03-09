"""Template rendering and atomic file writing."""
import filecmp
import json
import logging
import os
import shutil
import subprocess
import tempfile
import tomllib

import jinja2

from cloudyhome.constants import RUN_DIR, TEMPLATE_DIR

log = logging.getLogger(__name__)


def get_jinja_env(template_dir=TEMPLATE_DIR):
    """Create a Jinja2 environment."""
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_dir),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )


def render_template(name, context, template_dir=TEMPLATE_DIR):
    """Render a named template with the given context."""
    env = get_jinja_env(template_dir)
    template = env.get_template(name)
    return template.render(**context)


def validate_nft(path):
    """Validate nftables config via dry-run."""
    result = subprocess.run(
        ["nft", "-c", "-f", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"nftables validation failed: {result.stderr}")


def validate_samba(path):
    """Validate Samba config via testparm."""
    result = subprocess.run(
        ["testparm", "-s", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Samba validation failed: {result.stderr}")


def validate_toml(path):
    """Validate TOML syntax."""
    with open(path, "rb") as f:
        try:
            tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise RuntimeError(f"TOML validation failed: {e}")


def validate_iscsi_saveconfig(path):
    """Validate iSCSI saveconfig.json structure."""
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"iSCSI saveconfig is not valid JSON: {e}")
    for key in ("fabric_modules", "storage_objects", "targets"):
        if key not in data:
            raise RuntimeError(f"iSCSI saveconfig missing required key: {key}")
    for target in data["targets"]:
        if "wwn" not in target:
            raise RuntimeError(f"iSCSI target missing 'wwn': {target}")
        if not target.get("tpgs"):
            raise RuntimeError(f"iSCSI target {target.get('wwn')} has no TPGs")


def atomic_write(content, dest, mode=0o644, dir_mode=0o755, owner="root:root", validator=None, run_dir=RUN_DIR):
    """Write content to dest atomically via temp file, with optional validation.

    Returns True if the file was updated, False if unchanged.
    """
    os.makedirs(os.path.dirname(dest), mode=dir_mode, exist_ok=True)
    os.makedirs(run_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=run_dir, prefix="render.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)

        if validator:
            validator(tmp_path)

        # Compare with existing file
        if os.path.exists(dest) and filecmp.cmp(tmp_path, dest, shallow=False):
            os.unlink(tmp_path)
            log.info("Unchanged: %s", dest)
            return False

        os.chmod(tmp_path, mode)
        user, group = owner.split(":")
        shutil.chown(tmp_path, user=user, group=group)
        shutil.move(tmp_path, dest)
        log.info("Updated: %s", dest)
        return True
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
