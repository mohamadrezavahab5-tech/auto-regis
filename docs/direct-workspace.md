# Shared Workspace: direct Google Sheets (1.3.22)

Each PC installs AutoReview, signs into CRM, and imports the original Google
service-account JSON in Connections. The existing Windows user-scoped DPAPI
store is reused automatically. Import validates the Google token endpoint and
PEM before replacing a previous key. The source JSON is never copied into app
configuration or the installer. Do not distribute another PC's `.dpapi` file.

The Google Sheets API must be enabled and the shared spreadsheet must grant
Editor access to the service account. No Workspace URL or web-app deployment is
used. The CRM session supplies the action's actor; the service account supplies
only Google authentication. Shared-key holders have the same Google permissions;
CRM actor labels are application-level audit identity, not a Google-enforced
per-person access boundary.

## Schema and migration

On the first successful direct connection, one atomic batch duplicates existing
`Workspace state` and `Workspace execution` into v6 baseline tabs and creates
`Workspace state v6`, `Workspace audit v6`, and `Workspace meta v6`. Existing tabs
and data remain intact. Headers are validated; partial or incompatible schemas
stop synchronization instead of clearing data. All coworkers should upgrade to
1.3.22 together: older clients writing the legacy state are not participants in
the new journal. Historical script files remain marked legacy in the repository
and are excluded from the installer.

Legacy connection keys are ignored for runtime routing. Local DB, CRM login,
browser profile, logs, exports, and personal settings remain in their existing
profile. The first direct sync republishes pending local state and retains
execution history conservatively.

## Concurrent operations

Sheets offers no conditional cell update or global client lock. A read/write/read
mutable claim cell cannot prevent two successful claimants. Instead,
`Workspace state v6` is an append-only operation journal. Each mutation includes
operation ID, payload hash, CRM actor, machine, client version, timestamp, and
expected revisions or claim token. One `spreadsheets.batchUpdate` appends the
mutation and its audit rows atomically. The authoritative state is the ordered
fold of these immutable rows over the baseline.

Clients reread the journal after writes. The first eligible execution claim wins;
later competing claims or stale decisions reduce to conflicts. Only the current
claim's actor, SMR, revision, and token can record completion. Uncertain claims
remain blocking; they do not expire into an unsafe automatic retry. Result and
error cells are repairable projections, not the authority. Identical operation
IDs replay their result; a different payload or actor conflicts. A lost write
response is reconciled before any retry. Concurrent duplicate requests can leave
duplicate journal envelopes, but only the first operation ID is applied.

Google documents atomic batch application in its
[Sheets batch guide](https://developers.google.com/workspace/sheets/api/guides/batch).
The journal relies on API append order and must not be sorted, edited, or deleted
manually. The v6 baseline is also immutable. Legacy state tabs are retained as
the migration snapshot, not live projections. First access replays the journal;
subsequent polls fetch only its new rows. Very long-lived workspaces may eventually
need a separately coordinated snapshot/compaction migration; this release never
truncates shared history automatically.

Network work stays on the existing background workers. Read retries are bounded
with jitter, UI sync has bounded backoff, and failed sync leaves local queues and
cache intact. Execution stops if the authoritative claim cannot be confirmed.

## Verification

Normal tests use an in-memory Google API transport, independent client caches,
and forced write interleavings. They never need live Google credentials.

Optional live health/schema/read check (may create additive v6 tabs and writes the
unchanged schema cell to verify Editor access; never creates execution claims):

```powershell
$env:AUTOREVIEW_LIVE_WORKSPACE = '1'
$env:AUTOREVIEW_LIVE_HOME = "$env:LOCALAPPDATA\AutoReview"
python -m pytest -q tests/test_workspace_live.py
```

Build uses an explicit public config/script allowlist, rejects credential fields,
private PEM keys, DPAPI files, databases, logs and backups, and bundles the existing
official google-auth stack with the app. Version metadata comes from
`autoreview/version.py` and the matching project version.
