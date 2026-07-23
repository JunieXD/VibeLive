# G002 Viewer Runtime Contract Freeze

This fixture freezes public wire terminology and shapes before runtime implementation.

## Versions and wire format

- HTTP and WebSocket transport use `PROTOCOL_VERSION = 2`.
- Audience configuration uses `AUDIENCE_CONTRACT_VERSION = 1`; it is not derived from transport.
- Public JSON uses `snake_case`, matching the FastAPI/OpenAPI wire format.
- Runtime terms are `persona template`, `viewer instance`, `audience mode`, `AI director`,
  `persona memory`, and `growth meme`. `audience_id` is not a viewer-instance identity.

## Canonical audience hash

The hash input contains only `audience_contract_version`, the normalized active mode, and referenced
normalized persona templates. It excludes `client_start_request_id` and the claimed `config_hash`.

Normalization happens before serialization:

1. Validate all values and materialize contract defaults.
2. Normalize strings to Unicode NFC and line endings to LF. Trim identifier and presentation
   fields; preserve other semantic whitespace.
3. Preserve `persona_ids` order because it breaks allocation ties. Sort the referenced `personas`
   array by `persona_id`. Object/map insertion order has no meaning.
4. Reject floats and non-JSON values. Counts, revisions, versions, weights, and timestamps are
   integers.
5. Serialize UTF-8 JSON with recursively sorted object keys, no insignificant whitespace, and
   non-ASCII characters unescaped. Hash those exact bytes with SHA-256 lowercase hex.

The backend recomputes the hash. A client claim never replaces backend normalization.

## Session and runtime invariants

- `client_start_request_id` plus the recomputed hash is idempotent: same key/hash returns the
  original Session; same key/different hash is a conflict.
- Persona mirror revisions are monotonic. Same revision/different content conflicts; older revisions
  reject. Mode overrides are snapshot-only.
- `viewer_count` is 1 through 32. `persona_ids` is an ordered, unique, enabled roster and each entry
  has one positive integer weight. Dangling, duplicate, disabled-only, or unweighted rosters reject.
- Allocation uses largest remainder. Equal remainders use roster order, then `persona_id`.
- A non-silent `CrowdDecision` selects exact live viewer-instance IDs inside the clamped activity
  band. Silence is explicit and selects none. Director output is never barrage.
- Each selected viewer instance creates one `ViewerGenerationRequest`. Production batching defaults
  off; no automatic fallback may enable it.
- Accepted barrage inherits viewer/persona identity from the request and carries immutable snapshot
  presentation fields.
- Realtime publishes committed persona-memory revision and growth-meme lifecycle events.
- Meme import is idempotent and acknowledges backend persistence before local recovery data may be
  retired.

## Migration

Migration `0002_viewer_runtime` extends `session_records` and `audience_profiles`, creates the five
tables listed in `g002_audience_contract_v1.json`, retains legacy `session_audiences` for
compatibility, and does not use it for new Sessions.
