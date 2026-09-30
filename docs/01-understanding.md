# AutoReview - first understanding (from the old scripts + 2 screen recordings)

## What the process is
Online merchants register (SMR-xxxx requests). A reviewer opens each request in NBO
(merchant-support/registration -> Details) and sets a status: APPROVE / EDIT NEEDED (+reason) / CANCEL (+reason).
The old scripts (PythonProject/action-test*.py, reject_engine.py, action-online-instore.py) automate it in 2 stages:
1. REVIEW (action-test4.py): per SMR -> read website address, bank-account holder name, NBO category from the NBO details page ->
   open the merchant site (SSL, alive, contact info, add-to-cart, product count via pagination/WooCommerce/sitemap/API/links) ->
   look the domain up on enamad.ir (exists, expired, owner name, category) -> rule engine -> write SMR/Status/Reason to an .xlsx.
2. ACT (action-online-instore.py): read the .xlsx and click Assign-to-me / Change Status / reason in NBO for every row.
   reject_engine.py: mass-CANCEL a list of SMRs as "duplicate request".
The team tracks everything in a Google Sheet ("Online-Instore", many colour-coded columns) fed by exports from NBO
(merchant-request-*.xlsx) and Dynamics 365 (Sale Team Review - Merchant Registrations).

## Decision rules found in evaluate_merchant (order matters)
no enamad -> EDIT; not https -> EDIT (bad URL); site down -> REJECT; enamad expired -> EDIT; bank-name mismatch -> EDIT;
enamad owner mismatch -> EDIT; no contact info -> EDIT; cannot add to cart -> EDIT; products < min (60, or 10 for services/education) ->
EDIT (sitemap missing / too few); gold category -> MANUAL; enamad site down -> MANUAL; enamad category mismatch -> MANUAL; else APPROVE.

## Problems in the old code (why "it must be rebuilt")
- 4 copies of a 2,000-line script (action-test, -test2, -test3 identical size; -test4 a fork); logic and browser code interleaved.
- Forces a DISABLED "Change Status" button to enabled (btn.disabled=false; btn.click()) -> can submit a status NBO did not allow.
- Blanket `except: pass`, errors turned into "EDIT_NEEDED: URL wrong" (a timeout marks a healthy merchant as bad).
- Reasons matched fuzzily (SequenceMatcher >= 0.5) and, if nothing matches, the RAW text is typed into NBO.
- No dry-run, no audit log, no idempotency (re-running re-clicks), no per-SMR result of the ACT stage, Excel used as a database
  with a thread lock; hard-coded category keyword table; a session cookie file (nbo_cookies.json) kept next to the code.
- Rules only read what the page shows once; no evidence (screenshots/JSON) is stored with each decision.

## Target design (draft)
core (pure rules, tested) | collectors (NBO page, merchant site, enamad) | executor (only acts on approved rows, dry-run first) |
storage (sqlite + evidence) | CLI now, Windows app later. Anything uncertain -> MANUAL_REVIEW, never a guess.

## Open questions
1. The meeting text (GPT transcript) is not in the AUTO folder - which requirements did management give?
2. Are the two Google Sheets readable by link (they showed a "suspicious file" banner in the video)? Which tabs are inputs vs outputs?
3. Which actions may run WITHOUT a human (approve? cancel? edit-needed?) and what is the daily volume?
4. Who owns the reason list, the category/minimum-product table and the enamad rules - can they change?
