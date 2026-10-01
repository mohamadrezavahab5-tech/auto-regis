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

10. 2026-10-01 owner: owner_mismatch_action = EDIT (confirmed). The owner's screenshot of NBO's Change Status dialog shows the radio options APPROVE / EDIT NEEDED (+ CANCEL) and the reason list with exactly the Persian labels found in NBO's front-end - so nbo_catalog labels equal what the dropdown shows.

11. 2026-10-01 owner (confirmed, replaces part of #9): NBO and CRM open INSIDE the app with the person's own login (NBO with
    their OTP). An AutoReview panel next to them SUGGESTS decisions; applying anything in NBO happens only after the person
    confirms. Sheets: in the shared Online-Instore sheet the app fills ONLY the 4 Online-group columns of the Pending tab
    (Date Online Check, بررسی قرارداد, دلیل نیاز به ادیت, دلیل لغو قرارداد), only where they are empty, with the sheet's own
    dropdown values - never another team's columns, never a new column or tab. All full results and logs go to the owner's
    own sheet. The app must always show how much of the day's work is done and left, and every status.
12. 2026-10-01 owner (confirmed, replaces the Online-Instore part of #11): the whole flow lives in the owner's OWN sheet
    ("AutoReview - Results"); the teams' shared sheets (Main-Data, Online-Instore) are never read or written - the old
    shared-sheet writer was removed from the code, and a test fails if any code path names another spreadsheet.
    - Online only: the Online verdict decides. Online + Instore: ready for NBO only when BOTH teams approved (the team used
      to write "approved" in the shared sheet, wait for Instore, then approve all in NBO).
    - The engine's APPROVE/EDIT/CANCEL counts as the Online verdict (Settings switch); MANUAL always waits for a person and
      any person can replace any verdict ("manual approval whenever needed").
    - Instore verdicts: in the app, or in the Instore columns of the "Online + Instore" tab of the owner's sheet (same words
      as the old dropdown); the app only writes its own columns there.
    - NBO's later status is kept: approved in NBO -> DONE_APPROVED, edit/cancel -> DONE_CLOSED, so done and left are always
      visible. Approval in NBO is still done by a person until an official NBO approval API is connected (live switch
      stays locked, owner only). CRM approve field recorded (new_merchantstatus = 100000005), execution disabled until the
      owner has CRM approval rights.
    - The app connects to the owner's sheet with his service-account key (DPAPI, his PC only) and syncs every 30 s while
      open. Colleagues: personal revocable codes via the Google-hosted service in his sheet (owner = mohammadreza.vahab).
    - Codex built most of this in a separate copy (2026-10-01 15:57-22:52); taken over with the owner's approval and completed.
