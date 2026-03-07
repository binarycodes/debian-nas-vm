"""Cross-field validation rules for NAS configuration."""
import ipaddress
import logging
from cloudyhome.secrets import resolve_ref

log = logging.getLogger(__name__)

RFC1918_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def is_rfc1918(addr_str):
    """Check if an IP or CIDR is within RFC1918 space."""
    try:
        network = ipaddress.ip_network(addr_str, strict=False)
        return any(
            network.subnet_of(rfc) for rfc in RFC1918_NETWORKS
        )
    except ValueError:
        return False


def validate_ip_rfc1918(addr_str, context=""):
    """Validate that an IP/CIDR is RFC1918. Returns error string or None."""
    if not is_rfc1918(addr_str):
        return f"Non-RFC1918 address '{addr_str}' in {context}"
    return None


def validate_email_domain(email, allowed_domains, context=""):
    """Validate email domain against allowed list. Returns error string or None."""
    if "@" not in email:
        return f"Invalid email (no @): '{email}' in {context}"
    domain = email.split("@", 1)[1].lower()
    if domain not in [d.lower() for d in allowed_domains]:
        return f"Email domain '{domain}' not in allowed domains in {context}"
    return None


def validate_all(config, secrets):
    """Run all cross-field validation rules. Returns list of error strings."""
    errors = []

    # Validate host IP
    try:
        host_ip = resolve_ref(secrets, config.host_ip_ref)
        err = validate_ip_rfc1918(host_ip, "host_ip_ref")
        if err:
            errors.append(err)
    except KeyError as e:
        errors.append(str(e))

    # Validate disks.ids
    try:
        disk_ids = resolve_ref(secrets, "disks/ids")
        if not isinstance(disk_ids, list) or not disk_ids:
            errors.append("disks.ids must be a non-empty list")
        elif any(not isinstance(d, str) or not d for d in disk_ids):
            errors.append("disks.ids must contain only non-empty strings")
    except KeyError:
        errors.append("disks.ids not found in secrets")

    # Validate firewall sources
    for rule in config.firewall.rules:
        try:
            sources = resolve_ref(secrets, rule.sources_ref)
            if not isinstance(sources, list) or not sources:
                errors.append(f"Firewall sources_ref '{rule.sources_ref}' must resolve to non-empty list")
            else:
                for src in sources:
                    # Add /32 for bare IPs for validation
                    addr = src if "/" in src else f"{src}/32"
                    err = validate_ip_rfc1918(addr, f"firewall rule '{rule.service}'")
                    if err:
                        errors.append(err)
        except KeyError as e:
            errors.append(f"Firewall rule '{rule.service}': {e}")

    # Path-to-dataset cross-validation
    dataset_paths = set(d.path for d in config.storage.datasets.values())
    dataset_keys = set(config.storage.datasets.keys())

    if config.nfs:
        for export in config.nfs.exports:
            if export.path not in dataset_paths:
                errors.append(f"NFS export path '{export.path}' not in storage.datasets")
            if export.enabled:
                for client in export.clients:
                    try:
                        cidrs = resolve_ref(secrets, client.cidr_ref)
                        if not isinstance(cidrs, list) or not cidrs:
                            errors.append(f"NFS cidr_ref '{client.cidr_ref}' must resolve to non-empty list")
                        else:
                            for cidr in cidrs:
                                addr = cidr if "/" in cidr else f"{cidr}/32"
                                err = validate_ip_rfc1918(addr, f"NFS export '{export.name}'")
                                if err:
                                    errors.append(err)
                    except KeyError as e:
                        errors.append(f"NFS export '{export.name}': {e}")

    if config.samba:
        for share in config.samba.shares:
            if share.path not in dataset_paths:
                errors.append(f"Samba share path '{share.path}' not in storage.datasets")
            if share.enabled:
                for ref_path in share.users_ref:
                    try:
                        resolved = resolve_ref(secrets, ref_path)
                        # Extract username from ref path
                        username = ref_path.split("/")[-1]
                        if not username.startswith("smb_"):
                            errors.append(f"Samba username '{username}' must be prefixed with smb_")
                    except KeyError as e:
                        errors.append(f"Samba share '{share.name}': {e}")
        # Validate all samba users have smb_ prefix
        try:
            samba_users = resolve_ref(secrets, "samba/users")
            if not isinstance(samba_users, dict) or not samba_users:
                errors.append("samba.users in secrets must be a non-empty map")
            else:
                for username in samba_users:
                    if not username.startswith("smb_"):
                        errors.append(f"Samba username '{username}' must be prefixed with smb_")
        except KeyError:
            errors.append("samba.users not found in secrets")

    if config.iscsi:
        for target in config.iscsi.targets:
            for lun in target.luns:
                # Parent dataset key = path minus last component
                parts = lun.path.split("/")
                if len(parts) >= 1:
                    parent_key = parts[0]
                    if parent_key not in dataset_keys:
                        errors.append(f"iSCSI LUN parent dataset key '{parent_key}' not in storage.datasets")
            if target.auth.session_auth == "chap":
                try:
                    chap = resolve_ref(secrets, target.auth.chap_secret_ref)
                    if not isinstance(chap, dict):
                        errors.append(f"iSCSI target '{target.name}' chap_secret_ref must resolve to a map")
                    else:
                        for field in ("chap_user", "chap_password"):
                            if not chap.get(field):
                                errors.append(f"iSCSI target '{target.name}' missing {field} in chap secret")
                except KeyError as e:
                    errors.append(f"iSCSI target '{target.name}': {e}")

    if config.garage:
        if config.garage.data_dir not in dataset_paths:
            errors.append(f"Garage data_dir '{config.garage.data_dir}' not in storage.datasets")
        if config.garage.metadata_dir not in dataset_paths:
            errors.append(f"Garage metadata_dir '{config.garage.metadata_dir}' not in storage.datasets")
        if config.garage.enabled:
            for ref_field in ("admin_token_ref", "rpc_secret_ref"):
                ref_path = getattr(config.garage, ref_field)
                try:
                    val = resolve_ref(secrets, ref_path)
                    if not val:
                        errors.append(f"Garage {ref_field} resolved to empty value")
                except KeyError as e:
                    errors.append(f"Garage {ref_field}: {e}")

    if config.ftp:
        if config.ftp.upload_root not in dataset_paths:
            errors.append(f"FTP upload_root '{config.ftp.upload_root}' not in storage.datasets")
        if config.ftp.enabled:
            for ref_path in config.ftp.users_ref:
                try:
                    resolve_ref(secrets, ref_path)
                except KeyError as e:
                    errors.append(f"FTP users_ref: {e}")
            try:
                ftp_users = resolve_ref(secrets, "ftp/users")
                if not isinstance(ftp_users, dict) or not ftp_users:
                    errors.append("ftp.users in secrets must be a non-empty map")
            except KeyError:
                errors.append("ftp.users not found in secrets")

    # Health / email validation
    if config.health and config.health.alert.enabled:
        try:
            smtp_auth = resolve_ref(secrets, config.health.alert.smtp_auth_ref)
            if not isinstance(smtp_auth, dict):
                errors.append("health.smtp_auth_ref must resolve to a map")
            else:
                for field in ("username", "password"):
                    if not smtp_auth.get(field):
                        errors.append(f"health.smtp_auth missing {field}")
        except KeyError as e:
            errors.append(f"health smtp_auth_ref: {e}")

        try:
            addresses = resolve_ref(secrets, config.health.alert.addresses_ref)
            if not isinstance(addresses, dict):
                errors.append("health.addresses_ref must resolve to a map")
            else:
                for field in ("from_address", "to_address"):
                    addr = addresses.get(field, "")
                    if not addr:
                        errors.append(f"health.addresses missing {field}")
                    elif "@" not in addr:
                        errors.append(f"health.addresses.{field} must contain @")
        except KeyError as e:
            errors.append(f"health addresses_ref: {e}")

        # Email domain policy
        allowed_domains = secrets.get("allowed_email_domains", [])
        if not allowed_domains:
            errors.append("allowed_email_domains must be non-empty when health.alert.enabled=true")
        else:
            # Check all email addresses
            try:
                addresses = resolve_ref(secrets, config.health.alert.addresses_ref)
                for field in ("from_address", "to_address"):
                    addr = addresses.get(field, "")
                    if addr and "@" in addr:
                        err = validate_email_domain(addr, allowed_domains, f"health.addresses.{field}")
                        if err:
                            errors.append(err)
            except KeyError:
                pass  # Already reported above

    return errors
