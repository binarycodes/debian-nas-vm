"""Central registry of all well-known paths and tuning constants for the cloudyhome NAS."""

# --- Port ranges ---
PORT_MIN      = 1001   # minimum for user-defined ports (below this requires well-known exception)
PORT_MAX      = 65535
SMTP_PORT_MIN = 1      # SMTP port may be any valid TCP port

# --- Firewall ---
FIREWALL_VALID_PROTOS        = frozenset({"tcp", "udp"})
FIREWALL_VALID_DEFAULT_INPUT = frozenset({"drop", "accept"})
FIREWALL_WELL_KNOWN_PORTS    = frozenset({21, 22, 25, 80, 443, 445})

# --- NFS ---
NFS_VERSION             = 4
NFS_VALID_IDENTITY_MODES = frozenset({"root_squash", "no_root_squash", "all_squash"})

# --- Samba ---
SAMBA_MIN_PROTOCOL = "SMB3_11"

# --- iSCSI ---
ISCSI_VALID_AUTH_MODES = frozenset({"none", "chap"})

# --- Container runtime ---
CONTAINER_RUNTIME = "podman-quadlet-root"

# --- Garage ---
GARAGE_REPLICATION_MODES = frozenset({"none", "1", "2", "3"})

# --- FTP ---
FTP_CONTROL_PORT = 21

# --- SMTP / health alert ---
SMTP_DEFAULT_PORT     = 587
SMTP_VALID_TLS_MODES  = frozenset({"starttls", "tls", "off"})

# --- NasConfig schema version ---
CONFIG_VERSION = 2

# --- Config and secrets inputs ---
SERVICES_PATH = "/var/lib/cloudyhome/nas/services.yml"
SECRETS_PATH  = "/var/lib/cloudyhome/nas/secrets.enc.yaml"

# --- Runtime directory (tmpfs, used for locks and decrypted secret temp files) ---
RUN_DIR = "/run/nas"

# --- Lock files ---
RENDER_LOCK = "/run/nas/render.lock"
APPLY_LOCK  = "/run/nas/apply.lock"

# --- Templates ---
TEMPLATE_DIR = "/etc/cloudyhome/templates"

# --- Systemd / quadlet directories ---
SYSTEMD_UNIT_DIR = "/etc/systemd/system"
QUADLET_DIR      = "/etc/containers/systemd"

# --- Rendered output paths ---
NFTABLES_CONF        = "/etc/nftables.conf"
NFS_EXPORTS          = "/etc/exports.d/cloudyhome.exports"
SAMBA_CONF           = "/etc/samba/smb.conf"
ISCSI_SAVECONFIG     = "/etc/target/saveconfig.json"
GARAGE_CONTAINER     = "/etc/containers/systemd/cloudyhome-garage.container"
FTP_CONTAINER        = "/etc/containers/systemd/cloudyhome-ftp.container"
ALERT_CONF           = "/etc/cloudyhome/health/alert.conf"
MSMTPRC              = "/etc/msmtprc"
APPLY_SERVICES_SCRIPT = "/etc/cloudyhome/nas-apply-services.sh"
ZFS_SCRUB_SERVICE     = "/etc/systemd/system/cloudyhome-zfs-scrub.service"

# --- ZFS dataset defaults ---
ZFS_DATASET_COMPRESSION    = "lz4"
ZFS_DATASET_ATIME          = "off"
ZFS_DATASET_DEDUP          = "off"
ZFS_DATASET_SYNC           = "standard"
ZFS_DATASET_CASESENSITIVITY = "sensitive"

# --- Garage bootstrap tuning ---
GARAGE_MAX_ATTEMPTS       = 30
GARAGE_POLL_INTERVAL      = 1
GARAGE_BOOTSTRAP_TIMEOUT  = 60
