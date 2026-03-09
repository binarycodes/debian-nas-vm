# Mesh Backup Idea (Friends NAS Network)

## Goal

Enable a group of friends running the same NAS software to hold encrypted backup copies of each other's data with high resilience to outages, power cuts, and long connectivity gaps.

## Core Model

1. Each node creates encrypted backups locally (e.g., restic) into a dataset.
2. Node takes an immutable snapshot of the backup dataset.
3. Node creates a distributable artifact from that snapshot.
4. Node generates a torrent for the artifact.
5. Node publishes torrent metadata on an HTTPS site protected by OAuth.
6. Friend nodes monitor that HTTPS feed and automatically pull new torrents.
7. Friend nodes download, verify, store, and seed encrypted artifacts.

## Why Snapshot-First

- Torrent payload should be immutable.
- Do not torrent a live mutable backup repository path directly.
- Snapshot/export artifacts provide consistent, verifiable content.

## Incremental Transfer Caveat

- Restic itself is incremental, but torrent efficiency depends on artifact shape.
- If you package a snapshot into one large monolithic file (for example `backup-v1.tar`), small logical changes can force large re-downloads.
- To preserve incremental transfer efficiency, prefer file-granular artifacts.

Recommended patterns:
- Torrent a restic repository file set (many files/chunks) rather than a single archive file.
- Or generate ZFS incremental streams (`zfs send -i`) between snapshots and torrent each incremental stream artifact.

Practical rule:
- Avoid monolithic export files when bandwidth efficiency matters.

## Security Model

- Backup data is encrypted before distribution.
- Storage peers do not need decryption keys to hold/seed data.
- HTTPS+OAuth controls access to release metadata.
- Signed manifests should be used for authenticity checks.

## Release Metadata (Suggested)

For each published backup release:
- `version`
- `created_at`
- `dataset`
- `snapshot_id`
- `artifact_size`
- `artifact_hash`
- `torrent_url` (or magnet + infohash)
- `signature`

## Interruption Resilience

This model is expected to handle interruptions better than star topology sync in unreliable networks:
- Piecewise resumable transfer.
- Multiple peers can serve missing pieces.
- Long offline periods are acceptable; nodes catch up later.
- No single central uploader/downloader bottleneck.

## Torrent Behavior Clarification

- A torrent represents a fixed snapshot of content.
- You generally do not append files to an existing torrent in place.
- New backup state should be published as a new snapshot + new torrent version.

## Torrent Basics for This Design

### Private Torrents

- `private` means peer discovery should stay controlled.
- DHT/PEX/local discovery are disabled in compliant clients.
- Peers are found via configured tracker(s) or explicitly configured peers.
- Recommended default for friend-only encrypted backup distribution.

### Announce URL

- The announce URL is the tracker endpoint included in the `.torrent` metadata.
- Example format: `https://tracker.example.com/announce`.
- The tracker coordinates peer discovery; it does not store backup payload data.

### Tracker Service Requirement

To use an announce URL, run a BitTorrent tracker service and expose its announce endpoint.

Common FOSS tracker options:
- `opentracker` (lightweight)
- `chihaya` (Go, modern/scalable)
- `XBT Tracker` (older but established)

Operational note:
- For private/friend-only swarms, combine private torrents with tracker auth and/or network restriction (VPN overlay, allowlist firewall rules).

### Minimal Torrent Creation Example

```bash
mktorrent -p -l 21 -n "backup-v1" \
  -a "https://tracker.example.com/announce" \
  -o backup-v1.torrent \
  /path/to/file1.bin /path/to/file2.bin
```

### Sample Docker Compose (Passkey-Authenticated Announce Model)

This reference stack runs:
- `tracker` (Chihaya, private internal announce backend)
- `announce-auth` (validates passkeys and proxies valid announces to tracker)
- `torrent-publish` (hosts per-user `.torrent` files and manifests)
- `gateway` (TLS entrypoint; OAuth for metadata site, passkey route for announce)

```yaml
services:
  tracker:
    # Internal Chihaya tracker backend (not exposed publicly).
    image: ghcr.io/chihaya/chihaya:latest
    container_name: mesh-tracker
    restart: unless-stopped
    volumes:
      - ./chihaya/chihaya.yaml:/etc/chihaya/chihaya.yaml:ro
    expose:
      - "6969"
    # Keep tracker announce path simple and internal.
    command: ["-config", "/etc/chihaya/chihaya.yaml"]

  announce-auth:
    # Small API that validates passkey and proxies to tracker:/announce.
    # Implement as your own FastAPI/Go service.
    image: ghcr.io/your-org/announce-auth:latest
    container_name: mesh-announce-auth
    restart: unless-stopped
    environment:
      # Where to send valid announce requests.
      TRACKER_UPSTREAM: "http://tracker:6969/announce"
      # DB for passkey -> user mapping and revocation checks.
      PASSKEY_DB_DSN: "postgresql://app:app@db:5432/announce"
    depends_on:
      - tracker
      - db
    expose:
      - "8081"

  db:
    # Stores friends, passkeys, and revocation state.
    image: postgres:16-alpine
    container_name: mesh-announce-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: announce
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
    volumes:
      - pg_data:/var/lib/postgresql/data

  torrent-publish:
    # Static host for per-user torrent files + manifests.
    image: nginx:alpine
    container_name: mesh-publish
    restart: unless-stopped
    volumes:
      - ./publish:/usr/share/nginx/html:ro
    expose:
      - "80"

  gateway:
    # Public HTTPS edge:
    # - /announce/<passkey> -> announce-auth service
    # - /torrents, /manifests -> static publisher (OAuth in front)
    image: caddy:2
    container_name: mesh-gateway
    restart: unless-stopped
    ports:
      - "443:443/tcp"
      - "80:80/tcp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - announce-auth
      - torrent-publish

volumes:
  caddy_data:
  caddy_config:
  pg_data:
```

Example `Caddyfile` routing:

```caddy
# Tracker hostname used in announce URL inside per-user torrent files.
tracker.example.com {
  # passkey route for BitTorrent clients (no OAuth redirect flow).
  reverse_proxy /announce/* announce-auth:8081
}

# Metadata/torrent host where friends fetch per-user .torrent files.
files.example.com {
  # Add OAuth/OIDC middleware here for private web/API access.
  reverse_proxy torrent-publish:80
}
```

Minimal `chihaya/chihaya.yaml` (internal HTTP announce backend):

```yaml
http:
  addr: "0.0.0.0:6969"
```

Notes:
- Chihaya stays internal; passkey validation happens in `announce-auth`.
- Do not put browser OAuth flow directly on `/announce`; torrent clients are non-interactive.
- Use per-user announce URLs: `https://tracker.example.com/announce/<passkey>`.

### How to Publish the `.torrent` File

1. Create directories:

```bash
mkdir -p publish/torrents publish/manifests chihaya scripts
```

2. Create a passkey for a friend (stored in DB):

```bash
cat > scripts/issue_passkey.sh <<'BASH'
#!/usr/bin/env bash
set -euo pipefail

friend_id="$1"
passkey="$(openssl rand -hex 32)"

# Example DB insert; adapt schema/columns to your service.
docker exec -i mesh-announce-db psql -U app -d announce <<SQL
insert into passkeys(friend_id, passkey, active, created_at)
values ('${friend_id}', '${passkey}', true, now());
SQL

echo "${passkey}"
BASH
chmod +x scripts/issue_passkey.sh
```

3. Build a per-user torrent with passkey-specific announce URL:

```bash
cat > scripts/build_user_torrent.sh <<'BASH'
#!/usr/bin/env bash
set -euo pipefail

friend_id="$1"
passkey="$2"
payload_path="$3"   # immutable snapshot/export artifact
version="$4"

announce_url="https://tracker.example.com/announce/${passkey}"
out_file="publish/torrents/${version}-${friend_id}.torrent"

mktorrent -p -l 21 -n "${version}" \
  -a "${announce_url}" \
  -o "${out_file}" \
  "${payload_path}"

echo "wrote ${out_file}"
BASH
chmod +x scripts/build_user_torrent.sh
```

4. Publish a matching manifest entry:

```bash
cat > publish/manifests/backup-v1-friend-a.json <<'JSON'
{
  "version": "backup-v1",
  "friend_id": "friend-a",
  "torrent_url": "https://files.example.com/torrents/backup-v1-friend-a.torrent",
  "artifact_hash": "<sha256>",
  "created_at": "2026-03-09T00:00:00Z"
}
JSON
```

5. Start services:

```bash
docker compose up -d
```

6. Verification flow at announce time:
- Client announces to `https://tracker.example.com/announce/<passkey>?...`.
- `announce-auth` checks passkey in DB and confirms `active=true`.
- If valid: proxy request to `http://tracker:6969/announce?...`.
- If invalid/revoked: return 403 and do not forward.

7. Rotation/revocation:
- Revoke by setting `active=false` for that passkey in DB.
- Issue a new passkey and publish a new per-user `.torrent` when needed.

## Comparison to Star Backup Sync

Mesh snapshot+torrent distribution is typically better for:
- Intermittent internet.
- Power cuts.
- Multi-day/multi-month disconnections.

Star topology may still be better for:
- Simpler operations/monitoring.
- Strong centralized governance and control.

## Product-Level Guardrails (if added to NAS platform)

- Disabled by default.
- Private/friend-only mode by default.
- Explicit UX warnings before public sharing.
- Audit logs for publish/subscribe operations.
- Retention policies to limit storage growth from many versions.

## Open Implementation Notes

- Define artifact format and publish cadence.
- Define subscriber polling interval/backoff strategy.
- Define retention and garbage collection across versions.
- Define restore workflow from replicated encrypted artifacts.
