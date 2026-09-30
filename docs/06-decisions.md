# 06 - Decisions of 2026-09-30/10-01 (owner)

1. Data sources stay the team's EXISTING sheets (Main-Data, Online-Instore). The app reads their exports; it writes into them only after the owner enables it.
2. **Nothing executes until the owner approves.** config/execution.json is closed (enabled=false); every side effect goes through autoreview/guard.py.
   Before going live the owner must tell the colleagues to STOP running their old scripts (two runners = double actions).
3. The owner's own results sheet: "AutoReview - Results" (private, owner's Google account)
   https://docs.google.com/spreadsheets/d/1UHlktMbe6bhDMKQd51Z-H1pvrrgD3ppTl9QmI1cEFrQ/edit
   Tabs: Results | Manual queue | Daily summary | Reasons (rule -> NBO code; the Persian dropdown label column is empty until read from NBO).
4. Everything runs in the background, no browser tabs: plain HTTP for websites, one headless browser only where unavoidable, one visible login (with OTP) by the owner.
5. Answers: min products per category (configurable); unreachable site = MANUAL; PENDING_ACTIVATION = approved; only the PENDING tab is worked.
6. Waiting on: NBO login (username/password + OTP from the manager) to read the Persian reason labels from the Change-Status dropdown (read-only; nothing confirmed).
7. Next build steps: enamad collector -> runner with progress/pause/resume -> optional write of results to the sheets (behind the guard) -> Windows app.