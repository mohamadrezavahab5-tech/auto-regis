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
8. 2026-10-01: NBO ships its own code->Persian-label maps for Edit and Cancel reasons in a lazy front-end chunk (read-only, no request was touched; 'Change Status' is disabled until 'Assign to me', which would change a real request, so it was NOT used). autoreview/nbo_catalog.py parses them; the executor must take labels from there and refuse when a code has none. MISSING_ENAMAD -> MISSING_LICENSE and CATEGORY_MISMATCH -> ENAMAD_CATEGORY_MISMATCH are now resolved. Open: 'enamad not shown on site' has no NBO code; Owner-name mismatch can be EDIT (OWNER_MISMATCH) or CANCEL (ENAMAD_OWNER_NAME_MISMATCH, 1099 in the sample) - owner decides.

9. 2026-10-01 owner: the Online-Instore sheet stays as the shared board for colleagues, but the app does NOT record into it (nor into Main-Data): all app output goes to the owner's own sheet 'AutoReview - Results'. 'Enamad not shown on site' = the same reason as no enamad (MISSING_LICENSE). Owner-name mismatch is configurable: rules.json owner_mismatch_action = EDIT | CANCEL | MANUAL (shipped: MANUAL until the owner chooses).
