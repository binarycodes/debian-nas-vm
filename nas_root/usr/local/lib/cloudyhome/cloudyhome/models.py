"""Pydantic v2 models for NAS configuration."""
import re
from typing import ClassVar, Optional
from pydantic import BaseModel, field_validator, model_validator, ConfigDict


class StorageDataset(BaseModel):
    path: str
    quota: str

    @field_validator("path")
    @classmethod
    def path_must_be_absolute_zpool(cls, v):
        if not v.startswith("/zpool0/"):
            raise ValueError("path must start with /zpool0/")
        return v

    @field_validator("quota")
    @classmethod
    def quota_must_be_valid(cls, v):
        if not re.match(r"^\d+[KMGT]$", v):
            raise ValueError("quota must be integer followed by K, M, G, or T")
        return v


class StorageConfig(BaseModel):
    pool: str
    datasets: dict[str, StorageDataset]

    @field_validator("pool")
    @classmethod
    def pool_must_be_zpool0(cls, v):
        if v != "zpool0":
            raise ValueError("pool must be zpool0")
        return v

    @field_validator("datasets")
    @classmethod
    def datasets_non_empty(cls, v):
        if not v:
            raise ValueError("datasets must be non-empty")
        return v

    @model_validator(mode="after")
    def unique_paths(self):
        paths = [d.path for d in self.datasets.values()]
        if len(paths) != len(set(paths)):
            raise ValueError("dataset paths must be unique")
        return self

    @model_validator(mode="after")
    def valid_dataset_keys(self):
        for key in self.datasets:
            if not re.match(r"^[a-z][a-z0-9_]*$", key):
                raise ValueError(f"dataset key '{key}' must be underscore-separated lowercase identifier")
        return self


class FirewallRule(BaseModel):
    service: str
    ports: Optional[list[int]] = None
    port_range: Optional[list[int]] = None
    proto: list[str]
    sources_ref: str

    @field_validator("proto")
    @classmethod
    def proto_must_be_valid(cls, v):
        if not v:
            raise ValueError("proto must be non-empty")
        for p in v:
            if p not in ("tcp", "udp"):
                raise ValueError(f"proto must be tcp or udp, got {p}")
        return v

    WELL_KNOWN_PORTS: ClassVar[set[int]] = {22, 445, 21}

    @field_validator("ports")
    @classmethod
    def ports_must_be_valid(cls, v):
        if v is not None:
            for p in v:
                if p in cls.WELL_KNOWN_PORTS:
                    continue
                if not (1001 <= p <= 65535):
                    raise ValueError(f"port {p} must be 1001-65535 or a well-known port (22, 445, 21)")
        return v

    @field_validator("port_range")
    @classmethod
    def port_range_must_be_valid(cls, v):
        if v is not None:
            if len(v) != 2:
                raise ValueError("port_range must be [min, max]")
            if not (1001 <= v[0] <= 65535 and 1001 <= v[1] <= 65535):
                raise ValueError("port_range values must be 1001-65535")
            if v[0] > v[1]:
                raise ValueError("port_range min must be <= max")
        return v

    @model_validator(mode="after")
    def ports_or_port_range(self):
        if self.ports is not None and self.port_range is not None:
            raise ValueError("cannot define both ports and port_range")
        if self.ports is None and self.port_range is None:
            raise ValueError("must define ports or port_range")
        return self

    @field_validator("sources_ref")
    @classmethod
    def sources_ref_non_empty(cls, v):
        if not v:
            raise ValueError("sources_ref must be non-empty")
        return v


class FirewallConfig(BaseModel):
    default_input: str
    rules: list[FirewallRule]

    @field_validator("default_input")
    @classmethod
    def default_input_must_be_valid(cls, v):
        if v not in ("drop", "accept"):
            raise ValueError("default_input must be drop or accept")
        return v

    @field_validator("rules")
    @classmethod
    def rules_non_empty(cls, v):
        if not v:
            raise ValueError("rules must be non-empty")
        return v

    @model_validator(mode="after")
    def unique_services(self):
        services = [r.service for r in self.rules]
        if len(services) != len(set(services)):
            raise ValueError("firewall rule service names must be unique")
        return self


class IdentityMap(BaseModel):
    mode: str = "root_squash"
    anon_uid: Optional[int] = None
    anon_gid: Optional[int] = None

    @field_validator("mode")
    @classmethod
    def mode_must_be_valid(cls, v):
        if v not in ("root_squash", "no_root_squash", "all_squash"):
            raise ValueError("mode must be root_squash, no_root_squash, or all_squash")
        return v

    @model_validator(mode="after")
    def all_squash_requires_anon(self):
        if self.mode == "all_squash":
            if self.anon_uid is None or self.anon_gid is None:
                raise ValueError("all_squash requires anon_uid and anon_gid")
        return self


class NfsClient(BaseModel):
    cidr_ref: str
    options: list[str] = []
    identity_map: IdentityMap = IdentityMap()

    @field_validator("options")
    @classmethod
    def no_identity_in_options(cls, v):
        forbidden = {"root_squash", "no_root_squash", "all_squash"}
        for opt in v:
            if opt in forbidden or opt.startswith("anonuid=") or opt.startswith("anongid="):
                raise ValueError(f"identity mapping directive '{opt}' must be in identity_map, not options")
        return v


class NfsExport(BaseModel):
    name: str
    path: str
    clients: list[NfsClient] = []
    options: list[str] = []
    enabled: bool = True

    @field_validator("options")
    @classmethod
    def no_identity_in_export_options(cls, v):
        forbidden = {"root_squash", "no_root_squash", "all_squash"}
        for opt in v:
            if opt in forbidden or opt.startswith("anonuid=") or opt.startswith("anongid="):
                raise ValueError(f"identity mapping directive '{opt}' must be in identity_map, not options")
        return v

    @model_validator(mode="after")
    def clients_required_when_enabled(self):
        if self.enabled and not self.clients:
            raise ValueError("clients must be non-empty when export is enabled")
        return self


class NfsConfig(BaseModel):
    version: int
    exports: list[NfsExport]

    @field_validator("version")
    @classmethod
    def version_must_be_4(cls, v):
        if v != 4:
            raise ValueError("NFS version must be 4")
        return v

    @field_validator("exports")
    @classmethod
    def exports_non_empty(cls, v):
        if not v:
            raise ValueError("exports must be non-empty")
        return v

    @model_validator(mode="after")
    def unique_names_and_paths(self):
        names = [e.name for e in self.exports]
        if len(names) != len(set(names)):
            raise ValueError("export names must be unique")
        paths = [e.path for e in self.exports]
        if len(paths) != len(set(paths)):
            raise ValueError("export paths must be unique")
        return self


class SambaGlobal(BaseModel):
    workgroup: str
    server_string: str = "CloudyHome NAS"
    min_protocol: str

    @field_validator("min_protocol")
    @classmethod
    def min_protocol_must_be_smb3(cls, v):
        if v != "SMB3_11":
            raise ValueError("min_protocol must be SMB3_11")
        return v


class SambaShare(BaseModel):
    name: str
    path: str
    browsable: bool = True
    read_only: bool = False
    guest_ok: bool = False
    users_ref: list[str] = []
    write_list: list[str] = []
    force_user: str = ""
    force_group: str = ""
    create_mask: str = "0660"
    directory_mask: str = "0770"
    enabled: bool = True

    @model_validator(mode="after")
    def users_ref_required_when_enabled(self):
        if self.enabled and not self.users_ref:
            raise ValueError("users_ref must be non-empty when share is enabled")
        return self


class SambaConfig(BaseModel):
    global_: SambaGlobal  # 'global' is reserved
    shares: list[SambaShare]

    model_config = ConfigDict(populate_by_name=True)

    # Accept 'global' from YAML
    def __init__(self, **data):
        if "global" in data:
            data["global_"] = data.pop("global")
        super().__init__(**data)

    @field_validator("shares")
    @classmethod
    def shares_non_empty(cls, v):
        if not v:
            raise ValueError("shares must be non-empty")
        return v

    @model_validator(mode="after")
    def unique_share_names(self):
        names = [s.name for s in self.shares]
        if len(names) != len(set(names)):
            raise ValueError("share names must be unique")
        return self


class IscsiLun(BaseModel):
    lun: int
    type: str
    path: str
    size: str
    readonly: bool = False

    @field_validator("type")
    @classmethod
    def type_must_be_zvol(cls, v):
        if v != "zvol":
            raise ValueError("LUN type must be zvol")
        return v

    @field_validator("size")
    @classmethod
    def size_must_be_valid(cls, v):
        if not re.match(r"^\d+[KMGT]$", v):
            raise ValueError("size must be integer followed by K, M, G, or T")
        return v


class IscsiAuth(BaseModel):
    discovery_auth: str
    session_auth: str
    chap_secret_ref: str = ""

    @field_validator("discovery_auth")
    @classmethod
    def discovery_auth_valid(cls, v):
        if v not in ("none", "chap"):
            raise ValueError("discovery_auth must be none or chap")
        return v

    @field_validator("session_auth")
    @classmethod
    def session_auth_valid(cls, v):
        if v not in ("none", "chap"):
            raise ValueError("session_auth must be none or chap")
        return v

    @model_validator(mode="after")
    def chap_ref_required(self):
        if self.session_auth == "chap" and not self.chap_secret_ref:
            raise ValueError("chap_secret_ref required when session_auth=chap")
        return self


class IscsiTarget(BaseModel):
    name: str
    iqn_suffix: str
    luns: list[IscsiLun] = []
    auth: IscsiAuth
    initiators: list[str] = []
    enabled: bool = True

    @model_validator(mode="after")
    def luns_required_when_enabled(self):
        if self.enabled and not self.luns:
            raise ValueError("luns must be non-empty when target is enabled")
        return self

    @model_validator(mode="after")
    def unique_lun_ids(self):
        if self.luns:
            ids = [l.lun for l in self.luns]
            if len(ids) != len(set(ids)):
                raise ValueError("LUN IDs must be unique per target")
        return self


class IscsiConfig(BaseModel):
    base_iqn: str
    portal_port: int
    targets: list[IscsiTarget]

    @field_validator("base_iqn")
    @classmethod
    def base_iqn_valid(cls, v):
        if not re.match(r"^iqn\.\d{4}-\d{2}\..+:.+$", v):
            raise ValueError("base_iqn must be in IQN format: iqn.YYYY-MM.<domain>:<string>")
        return v

    @field_validator("portal_port")
    @classmethod
    def portal_port_valid(cls, v):
        if not (1001 <= v <= 65535):
            raise ValueError("portal_port must be 1001-65535")
        return v

    @field_validator("targets")
    @classmethod
    def targets_non_empty(cls, v):
        if not v:
            raise ValueError("targets must be non-empty")
        return v

    @model_validator(mode="after")
    def unique_target_names_and_suffixes(self):
        names = [t.name for t in self.targets]
        if len(names) != len(set(names)):
            raise ValueError("target names must be unique")
        suffixes = [t.iqn_suffix for t in self.targets]
        if len(suffixes) != len(set(suffixes)):
            raise ValueError("iqn_suffix must be unique")
        return self

    @model_validator(mode="after")
    def unique_lun_paths(self):
        paths = []
        for t in self.targets:
            for l in t.luns:
                paths.append(l.path)
        if len(paths) != len(set(paths)):
            raise ValueError("LUN paths must be unique across all targets")
        return self


class PassivePorts(BaseModel):
    min: int
    max: int

    @model_validator(mode="after")
    def min_le_max(self):
        if self.min > self.max:
            raise ValueError("passive_ports min must be <= max")
        return self


class GarageConfig(BaseModel):
    enabled: bool = True
    runtime: str = "podman-quadlet-root"
    quadlet_name: str = "cloudyhome-garage"
    image: str
    rpc_port: int
    s3_port: int
    admin_port: int
    s3_region: str
    replication_mode: str
    config_dir: str
    data_dir: str
    metadata_dir: str
    layout_capacity: str
    admin_token_ref: str
    rpc_secret_ref: str

    @field_validator("runtime")
    @classmethod
    def runtime_valid(cls, v):
        if v != "podman-quadlet-root":
            raise ValueError("runtime must be podman-quadlet-root")
        return v

    @field_validator("replication_mode")
    @classmethod
    def replication_mode_valid(cls, v):
        if v not in ("none", "1", "2", "3"):
            raise ValueError("replication_mode must be one of: none, 1, 2, 3")
        return v

    @field_validator("data_dir")
    @classmethod
    def data_dir_under_zpool(cls, v):
        if not v.startswith("/zpool0/"):
            raise ValueError("data_dir must start with /zpool0/")
        return v

    @field_validator("metadata_dir")
    @classmethod
    def metadata_dir_under_zpool(cls, v):
        if not v.startswith("/zpool0/"):
            raise ValueError("metadata_dir must start with /zpool0/")
        return v

    @model_validator(mode="after")
    def enabled_requires_fields(self):
        if self.enabled:
            for field in ("quadlet_name", "image", "admin_token_ref", "rpc_secret_ref",
                         "config_dir", "s3_region", "layout_capacity"):
                if not getattr(self, field):
                    raise ValueError(f"{field} required when garage is enabled")
            if not self.config_dir.startswith("/"):
                raise ValueError("config_dir must be absolute path")
            for port_field in ("rpc_port", "s3_port", "admin_port"):
                p = getattr(self, port_field)
                if not (1001 <= p <= 65535):
                    raise ValueError(f"{port_field} must be 1001-65535")
        return self


class FtpConfig(BaseModel):
    enabled: bool = True
    runtime: str = "podman-quadlet-root"
    quadlet_name: str = "cloudyhome-ftp"
    image: str
    config_dir: str
    control_port: int
    passive_ports: PassivePorts
    users_ref: list[str]
    upload_root: str

    @field_validator("runtime")
    @classmethod
    def runtime_valid(cls, v):
        if v != "podman-quadlet-root":
            raise ValueError("runtime must be podman-quadlet-root")
        return v

    @field_validator("control_port")
    @classmethod
    def control_port_must_be_21(cls, v):
        if v != 21:
            raise ValueError("control_port must be 21")
        return v

    @field_validator("upload_root")
    @classmethod
    def upload_root_under_zpool(cls, v):
        if not v.startswith("/zpool0/"):
            raise ValueError("upload_root must start with /zpool0/")
        return v

    @model_validator(mode="after")
    def enabled_requires_fields(self):
        if self.enabled:
            for field in ("quadlet_name", "image", "config_dir"):
                if not getattr(self, field):
                    raise ValueError(f"{field} required when ftp is enabled")
            if not self.config_dir.startswith("/"):
                raise ValueError("config_dir must be absolute path")
            if not self.users_ref:
                raise ValueError("users_ref must be non-empty when ftp is enabled")
        return self


class HealthAlert(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_tls: str = "starttls"
    smtp_auth_ref: str = ""
    addresses_ref: str = ""

    @field_validator("smtp_tls")
    @classmethod
    def smtp_tls_valid(cls, v):
        if v not in ("starttls", "tls", "off"):
            raise ValueError("smtp_tls must be starttls, tls, or off")
        return v

    @model_validator(mode="after")
    def enabled_requires_fields(self):
        if self.enabled:
            if not self.smtp_host:
                raise ValueError("smtp_host required when alert is enabled")
            if not (1 <= self.smtp_port <= 65535):
                raise ValueError("smtp_port must be 1-65535")
            if not self.smtp_auth_ref:
                raise ValueError("smtp_auth_ref required when alert is enabled")
            if not self.addresses_ref:
                raise ValueError("addresses_ref required when alert is enabled")
        return self


class HealthConfig(BaseModel):
    alert: HealthAlert = HealthAlert()


class NasConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    host_ip_ref: str
    storage: StorageConfig
    firewall: FirewallConfig
    nfs: Optional[NfsConfig] = None
    samba: Optional[SambaConfig] = None
    iscsi: Optional[IscsiConfig] = None
    garage: Optional[GarageConfig] = None
    ftp: Optional[FtpConfig] = None
    health: Optional[HealthConfig] = None

    @field_validator("version")
    @classmethod
    def version_must_be_1(cls, v):
        if v != 1:
            raise ValueError("version must be 1")
        return v

    @field_validator("host_ip_ref")
    @classmethod
    def host_ip_ref_non_empty(cls, v):
        if not v:
            raise ValueError("host_ip_ref must be non-empty")
        return v


class SecretsConfig(BaseModel):
    """Model for the decrypted secrets file. Loosely typed since structure varies."""
    model_config = ConfigDict(extra="allow")
