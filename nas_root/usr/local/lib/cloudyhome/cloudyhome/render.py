"""Template rendering and atomic file writing."""
import filecmp
import logging
import os
import shutil
import subprocess
import tempfile

import jinja2

log = logging.getLogger(__name__)

TEMPLATE_DIR = "/etc/cloudyhome/templates"


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


def atomic_write(content, dest, mode=0o644, owner="root:root", validator=None, run_dir="/run/nas"):
    """Write content to dest atomically via temp file, with optional validation.

    Returns True if the file was updated, False if unchanged.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
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
