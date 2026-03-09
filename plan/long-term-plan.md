# Long-Term Plan

## Scope

Future capabilities beyond MVP2/MVP3 focused on data access and operational usability.

## Planned Items

1. WebDAV support
- Expose selected datasets over WebDAV/WebDAVS.
- Support authentication, TLS, and per-dataset access controls.
- Keep configuration declarative and API-manageable.

2. File management UI
- Provide a browser-based file management interface for datasets.
- Include core operations: browse, upload, download, move, copy, rename, and delete.
- Integrate with existing auth and permissions.

3. USB / Camera SD ingest with UI job tracking
- Auto-detect removable media and create an ingest job automatically.
- Require first-connect device registration (API/UI/Terraform) before automated ingest is allowed.
- Bind each registered device to:
  - a target backup dataset (where device contents are ingested), and
  - an optional copy dataset (source used to repopulate the device after backup flow).
- Show real-time progress in UI (bytes/files, speed, ETA, current stage).
- Use explicit pipeline states: detected -> backing_up -> verifying_backup -> snapshotting -> wipe_decision -> optional_wipe -> optional_repopulate_from_copy_dataset -> done/failed.
- Provide clear completion signal ("safe to unplug") plus optional notification.
- Persist ingest history (device id/label, destination, checksum summary, snapshot id, timestamps).
- Include failure handling and resume/retry controls from UI.
- Add per-device `wipe_on_backup` policy flag (on/off) applied after successful verify+snapshot.
- If copy dataset is configured, copy its contents to the device after backup stage (and after wipe when enabled).

Policy model (per-user):
- `user_id`: owner of the mapping and job visibility scope.
- `device_fingerprint`: stable device identity (serial + filesystem UUID + vendor/product where available).
- `backup_dataset`: destination dataset for ingest from removable media.
- `copy_dataset` (optional): source dataset used to repopulate device content after backup flow.
- `wipe_on_backup`: boolean gate for post-backup wipe.
- `enabled`: boolean toggle for active/inactive policy.

Constraint:
- This feature applies only to removable media attached directly to the NAS host (USB/SD physically connected to NAS), not browser-only or remote client-attached media flows.

4. LAN-only emergency portal
- Provide a local-network-only recovery/admin portal for break-glass operations when internet/IdP is unavailable.
- Restrict access to LAN interfaces only (no WAN exposure) with explicit firewall enforcement.
- Support offline authentication fallback suitable for emergency use.
- Scope features to recovery-critical actions (service status, storage health, mount/import checks, controlled recovery actions).
- Require strong audit logging for all emergency portal actions.
