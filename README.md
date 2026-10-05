# AutoReview

Standalone project (NOT part of PayPilot). Goal: rebuild the responsible person's messy scripts into a clean, tested tool; later wrap it in a Windows app.

## Input folders (drop files here)
- input\videos       the 2 silent screen recordings (they show the manual workflow)
- input\code         the existing scripts, untouched (copy, never edit the originals)
- input\sheets       the 2 sheets (export as .xlsx/.csv, or share the link)
- input\transcripts  the GPT text of the meeting audio (management requirements)

## Working rules
- Sources are read-only. Nothing here writes back to a sheet or to NBO without an explicit go-ahead.
- No passwords or tokens in files. NBO is used only through a session the user logs in to personally.
- Steps: 1) understand the workflow (videos + transcripts)  2) map the old code to it  3) write the spec  4) rebuild with tests  5) Windows app.

## Running the app (Windows)
- `dist\AutoReview\AutoReview.exe` (build: `powershell -File build.ps1`). `config\` and `scripts\` sit next to the exe and stay editable; results go to `data\`.
- When running from source, use a standard GIL-enabled Python interpreter; `run_app.py` redirects the free-threaded interpreter to the regular `python.exe` alongside it when available.
- Review and suggestions do not change NBO. A status change is sent only after an explicit action in the app; it is verified against the latest NBO data first.
- With workflow sync enabled, each installation syncs its local queue with the owner's sheet about every 10 seconds. The dashboard shows the latest sync time and reports stale local data or sync failures instead of treating them as current.
- For a team, each PC imports the original Service Account JSON in Connections and signs into CRM. DPAPI stores the key locally; existing keys are reused. No Apps Script URL or deployment is required. See [direct Workspace setup and concurrency](docs/direct-workspace.md).
- To publish an update, the owner selects the installer in «کاربران», uploads it to their own Google Drive, and sets sharing to `Anyone with the link — Viewer`. Paste the file link into the app to record the release URL and SHA-256 in the `Updates` tab. Installed apps check for updates after startup and every six hours; the downloaded installer is verified before the user confirms installation.
