# MVP4: Web UI

## Overview

MVP4 adds a browser-based management interface for the NAS. The web UI consumes the same API from MVP2.

## Prerequisites

- MVP2 (API) and MVP3 (Terraform provider) complete and stable.

## Authentication

MVP2/MVP3 use a static bearer token (baked in at bootstrap). This is sufficient for Terraform and automation but not for a web UI. MVP4 will need a login flow:

- `POST /v1/auth/login` — accepts credentials, returns a session token or JWT.
- The static bootstrap token continues to work as a root/automation key.
- Session management, expiry, and refresh TBD.

## Planned Items

TBD — scope to be defined when MVP3 is complete.
