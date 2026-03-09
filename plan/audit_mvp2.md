# MVP2 Plan Audit

Date: 2026-03-09
Source reviewed: `plan/mvp2.md`, `plan/questions.md`

## Summary

The MVP2 plan is strong overall (clear architecture, phased delivery, and reuse of MVP1 logic), but there are several high-impact inconsistencies and contract gaps that should be resolved before implementation proceeds.

## Findings

### 1. Firewall enforcement contradiction

Problem:
- `mvp2.md` states the management API firewall rule "must always be present" and includes a pinned-rule guard in Phase 2.
- `questions.md` Q7 is decided as operator responsibility with no enforcement.

References:
- `plan/mvp2.md` lines 275 and 326
- `plan/questions.md` lines 66-70

Impact:
- Conflicting implementation direction and test expectations.

Action:
- Pick one behavior and align all docs/phase items.

### 2. Error semantics are too coarse for Terraform-style consumers

Problem:
- `mvp2.md` currently maps broad apply failures to `500`.
- API consumers (especially Terraform) need deterministic, machine-readable error classes to separate retryable vs non-retryable failures.

References:
- `plan/mvp2.md` lines 201-215

Impact:
- Provider retry/drift behavior will be brittle.

Action:
- Add a precise error-code catalog and status mapping (e.g., lock/contention/dependency/transient-system).

### 3. Failure guarantees overstate atomicity

Problem:
- The flow performs side effects before writing `services.yml`.
- The plan also claims that on failure, running services are not changed.

References:
- `plan/mvp2.md` lines 235-243

Impact:
- Statement is not guaranteed unless rollback is implemented.

Action:
- Either add rollback scope or explicitly state "best-effort apply; partial side effects possible on failure."

### 4. Q9 (users API) is not represented in MVP2 scope/phase plan

Problem:
- `questions.md` defines a likely need for `/v1/users` and delete behavior policy.
- `mvp2.md` endpoint table and phases do not include this work.

References:
- `plan/questions.md` lines 84-91
- `plan/mvp2.md` endpoint and phase sections

Impact:
- User lifecycle debt accumulates with no planned delivery path.

Action:
- Decide in/out of MVP2 and add explicit endpoints + phase tasks (or mark out-of-scope with rationale).

### 5. Q4 decision (config download endpoints) is missing from API design

Problem:
- `questions.md` Q4 says API exposes download for current `services.yml` and `secrets.enc.yaml`.
- `mvp2.md` endpoint list does not include these endpoints.

References:
- `plan/questions.md` line 43
- `plan/mvp2.md` endpoint table

Impact:
- Disaster-recovery/rebake automation path is underspecified.

Action:
- Add explicit read-only endpoints, auth requirements, and response format.

### 6. Idempotency contract is not explicit for create endpoints

Problem:
- `POST` behavior on already-existing resources is not consistently defined.

Impact:
- Ambiguous client behavior, especially for Terraform/provider retries.

Action:
- Define per-resource create semantics explicitly (e.g., return existing resource vs `409`).

### 7. Collection endpoint scale behavior is unspecified

Problem:
- List endpoints do not document pagination or sort order.

Impact:
- Non-deterministic diffs and future breaking changes when resource counts grow.

Action:
- Define stable default ordering now and reserve pagination parameters (`limit`, `cursor` or `offset`).

### 8. Observability correlation is missing from API contract

Problem:
- No request/operation correlation ID is defined for logs and errors.

Impact:
- Harder to debug multi-step failures across API, render/apply pipeline, and systemd logs.

Action:
- Add `X-Request-ID` support and include correlation IDs in error envelopes/log lines.

### 9. Auth boundary for read-only endpoints needs explicit policy

Problem:
- `GET /v1/health` is no-auth; future read-only endpoints (config download) are not policy-defined.

Impact:
- Risk of accidental data exposure if read-only endpoints are treated as "safe by default."

Action:
- Document auth requirements per read-only endpoint and keep only liveness unauthenticated.

### 10. Secret exposure controls are not specified for export responses

Problem:
- Planned config export behavior includes secret-bearing material decisions that are not documented.

Impact:
- Potential leakage through API responses, logs, or backups.

Action:
- Define redaction/allowlist rules, response content policy, and audit logging for sensitive downloads.

### 11. Liveness vs readiness is not separated

Problem:
- The plan defines unauthenticated `GET /v1/health`, but does not define whether it reports only process liveness or full dependency readiness (secrets decrypted, config loadable, etc.).

References:
- `plan/mvp2.md` lines 199 and 318

Impact:
- Automation may treat the API as healthy before it can safely process mutating requests.

Action:
- Keep `/v1/health` as liveness and add a gated readiness endpoint (or explicitly define health semantics and failure states).

### 12. Lock acquisition order is not explicitly standardized

Problem:
- The plan requires acquiring both `RENDER_LOCK` and `APPLY_LOCK`, but does not state a canonical acquisition order.

References:
- `plan/mvp2.md` lines 219 and 317

Impact:
- Future code paths can introduce lock-order inversions and deadlock risk.

Action:
- Define and document one global lock order, then enforce it in shared helper code.

### 13. PUT path/body identity conflict handling is unspecified

Problem:
- Endpoints use path identifiers (`{key}`, `{name}`, `{service}`) while resource objects also include `id`; behavior for mismatches is not defined.

References:
- `plan/mvp2.md` lines 138-192

Impact:
- Inconsistent update semantics and avoidable client/provider drift bugs.

Action:
- Define explicit rules (path is source of truth; body id optional/forbidden; mismatch returns validation error).

### 14. Long-running mutation behavior is not defined

Problem:
- Mutating requests hold locks for full validate->render->apply duration while running shell/system operations.

References:
- `plan/mvp2.md` lines 219 and 235-239

Impact:
- Lock contention spikes and client timeouts under slower storage/service restart conditions.

Action:
- Define timeout policy, response behavior for long operations, and whether async job mode is needed later.

### 15. Phase 0 migration has rollout-order risk

Problem:
- Plan deletes existing Python scripts/library paths and switches systemd/install paths in the same phase without a compatibility bridge.

References:
- `plan/mvp2.md` lines 309-312

Impact:
- Partial packaging/install failures can break boot-chain executables.

Action:
- Add a transition strategy (compat symlinks/shims or strict atomic packaging/install verification gates).

### 16. Failure-injection coverage is not explicitly required

Problem:
- Testing mentions unit/integration/e2e but does not explicitly require fault injection at each pipeline step.

References:
- `plan/mvp2.md` lines 328, 334, 341, 345

Impact:
- High-risk failure paths (render/apply/reload/write) may remain unverified.

Action:
- Add a failure matrix with required tests for lock contention, render failure, apply failure, write failure, and restart failure.
