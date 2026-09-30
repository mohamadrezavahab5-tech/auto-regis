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
