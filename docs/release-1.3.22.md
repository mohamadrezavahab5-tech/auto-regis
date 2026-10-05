# AutoReview 1.3.22 verification

## Installer packaging repair

The initial setup archive inadvertently collected Poppler's `icuuc.dll`; Qt needs
the Windows ICU exports. A launcher restored PATH after the PowerShell cleanup,
so that cleanup did not protect PyInstaller. `installer/freeze.py` now sanitizes
PATH inside the Python build process. Verification rejects any collected Poppler
binary and runs the actual setup EXE with `--self-test`, requiring successful Qt
window rendering and an explicit completion file. A live process alone is not
startup proof, because an import-error dialog can keep that process alive.

The installer repair does not change Workspace behavior or the app version.

## Changes

- `workspace_google.py`: direct Sheets API backend, validated additive v6 schema,
  legacy state/execution snapshots, incremental reads, CRM audit metadata,
  deterministic append-journal concurrency, hashed operation receipts, source
  deduplication, revision conflicts, claim arbitration and holder-only completion.
- `workspace.py`: stable facade using the authenticated CRM identity, machine ID
  and central app version; durable execution receipts remain in the local DB.
  Receipts from another CRM login are preserved until their owner returns.
- `workflow_sync.py`: direct backend routing, safe first-sync migration, bounded
  source chunks, generation caching and retries with stable operation IDs after
  ambiguous failures. A confirmed source conflict gets a fresh retry ID.
- `google_credentials.py`: reuse/import Windows DPAPI credentials; reject malformed
  or foreign-endpoint credentials before replacing a working key.
- `sheets.py`: migrate connection mode without using old URLs or application keys;
  preserve personal data and local cache. Reporting also uses the direct API.
- Connections page: Service Account import, local credential status and background
  shared read/write health check. Existing styling and other connection cards stay.
- Build: explicit public-resource allowlist, credential/backup rejection, official
  Google dependencies bundled, consistent 1.3.22 metadata and bytecode-to-source
  verification of the generated app. Legacy `.gs` files stay in the repository,
  marked reference-only, and are not shipped.

## Checks completed

- Complete final suite: **313 passed, 1 skipped**. The skipped check is deliberately
  opt-in; normal tests have no live Google dependency.
- Optional live Workspace check: **1 passed**, using the existing locally encrypted
  credentials. Google authentication, expected spreadsheet identity, schema,
  write permission and shared read all succeeded. Additive v6 migration completed.
- Two independent mocked clients: concurrent decisions, source conflicts, claim
  winner/loser, holder-only finish, idempotency and lost-response reconciliation.
- Local cache/outbox preservation, migration, DPAPI, connection status, and resource
  security tests passed. Browser execution tests used the local fake NBO server.
- Runtime scan of `autoreview/**/*.py` and `run_app.py`: no `script.google.com`,
  `workspace_url`, `webapp_url`, `UrlFetchApp` or Apps Script references. The two
  remaining `app_key` mentions only strip/redact legacy secrets; neither sends one.
- Built app started successfully with a separate empty test profile. Windows
  version resource reports **1.3.22**. No existing login/profile was used.
- Build verification checks current module bytecode, Google dependencies, public
  config, forbidden personal files and the exact payload embedded in the installer.
  Machine-readable result and SHA-256: `build/verification-1.3.22.json`.

## Deployment and limits

Upgrade all participating PCs to this version together. Each PC imports the
original service-account JSON locally, unless its DPAPI key already exists, and
logs into its own CRM account. The Google API must be enabled and the account must
be Sheet Editor. No Apps Script deployment or Workspace URL is required.

The two-PC behavior was tested with independent simulated clients; a physical
two-PC end-to-end session was not performed. Live verification exercised Google
health and reads without creating test cases or executing real NBO actions.

The new journal/baseline tabs are application-owned immutable history. Old clients
still writing legacy tabs do not join the new Workspace. Long-term history
compaction is intentionally not automatic. See [backend details](direct-workspace.md).
