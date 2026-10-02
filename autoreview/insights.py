"""Numbers for the manager (owner 2026-10-02): how often the team agreed with the engine, which rule people overrule most,
and how the queue moves - speed, when it will be empty, what has waited longest, who did what. Pure functions over the
workflow cases and events; nothing here changes anything."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import jalali, workflow

HUMAN = ("human", "sheet", "workspace")
DECIDED = ("APPROVE", "EDIT", "CANCEL")
UNDECIDED = ("WAIT_ONLINE", "MANUAL", "CONFLICT", "WAIT_INSTORE")


def _when(text):
    try:
        d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _rule(suggestion):
    """The rule behind an engine verdict: the NBO reason of an edit/cancel, or APPROVE."""
    if suggestion.get("action") == "APPROVE":
        return "APPROVE"
    codes = suggestion.get("reason_codes") or []
    return codes[0] if codes else suggestion.get("action")


# ---- 2. engine accuracy -------------------------------------------------------------------------------------------------------
def accuracy(cases):
    """People's Online verdicts against the engine's, plus NBO's final word where the engine's verdict stood alone."""
    compared, agreed = 0, 0
    by_rule = defaultdict(lambda: [0, 0])                 # rule -> [compared, overruled]
    manual = Counter()                                    # what people decided where the engine said MANUAL
    disagreements = []
    outcome = [0, 0]                                      # [engine verdicts NBO settled, ... that NBO confirmed]
    for c in cases:
        s, o = c.get("suggestion") or {}, c.get("online") or {}
        if s.get("action") == "MANUAL" and o.get("source") in HUMAN and o.get("action") in DECIDED:
            manual[o["action"]] += 1
        if s.get("action") not in DECIDED:
            continue
        if o.get("source") in HUMAN and o.get("action") in DECIDED:
            compared += 1
            rule = by_rule[_rule(s)]
            rule[0] += 1
            if o["action"] == s["action"]:
                agreed += 1
            else:
                rule[1] += 1
                disagreements.append(dict(smr=c["smr"], site=c.get("site", ""), engine=s["action"], engine_reasons=s.get("reason_codes") or [],
                                          human=o["action"], human_reason=o.get("reason", ""), actor=o.get("actor", ""),
                                          note=o.get("note", ""), at=o.get("at", "")))
        elif o.get("source") == "engine" and c.get("outcome") in ("approved", "closed"):
            outcome[0] += 1
            if (c["outcome"] == "approved") == (s["action"] == "APPROVE"):
                outcome[1] += 1
    disagreements.sort(key=lambda d: d["at"], reverse=True)
    return dict(compared=compared, agreed=agreed, rate=(100.0 * agreed / compared) if compared else None,
                by_rule=sorted(((r, n, over) for r, (n, over) in by_rule.items()), key=lambda x: (-x[2], -x[1])),
                manual=dict(manual), outcome_settled=outcome[0], outcome_confirmed=outcome[1], disagreements=disagreements[:50])


# ---- 3. control room ----------------------------------------------------------------------------------------------------------
def events(db):
    return [json.loads(b) for (b,) in db.execute("SELECT body FROM workflow_events ORDER BY rowid")]


def control_room(cases, evs, results, created_at, approved_statuses=(), now=None, days=14):
    """cases: workflow.cases(); evs: events(); results: [(decided_at, action)] engine results; created_at: {smr: Jalali date}."""
    now = now or datetime.now(timezone.utc)
    approved = set(approved_statuses or ())
    states = Counter(c.get("state") or workflow.state(c) for c in cases)
    today = now.astimezone().date()
    day_keys = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    daily = {d: Counter() for d in day_keys}

    def bucket(at):
        w = _when(at)
        return w.astimezone().date() if w else None

    for at, _action in results:
        d = bucket(at)
        if d in daily:
            daily[d]["engine"] += 1
    people = Counter()
    week_ago = now - timedelta(days=7)
    finished = {}
    for e in evs:
        kind, d = e.get("kind"), bucket(e.get("at"))
        if kind == "WORKFLOW_HUMAN_DECISION":
            if d in daily:
                daily[d]["human"] += 1
            w = _when(e.get("at"))
            if w and w >= week_ago and e.get("actor"):
                people[e["actor"]] += 1
        elif kind == "WORKFLOW_NBO_STATUS" and (e.get("detail") or {}).get("status") is not None:
            status = e["detail"]["status"]
            if status in approved or status not in ("PENDING", "COMMERCIAL_IN_PROGRESS"):
                finished[e["smr"]] = _when(e["at"])
                if d in daily:
                    daily[d]["nbo"] += 1
    engine_week = sum(1 for at, _ in results if (_when(at) or now) >= week_ago)
    if engine_week:
        people["موتور (AutoReview)"] += engine_week

    undecided = sum(states.get(k, 0) for k in UNDECIDED)
    # The undecided ones wait on people (a manual review, the Instore verdict): only people's decisions empty that queue.
    # Counting the engine's ~1,000 reviews a day here promised "less than a day" while people had decided nothing
    # (live 2026-10-03). No human decision in 7 days -> no estimate rather than an invented one.
    active_days = [daily[d]["human"] for d in day_keys[-7:]]
    worked = [n for n in active_days if n]
    speed = sum(worked) / len(worked) if worked else None             # people's decisions per working day
    eta_days = (undecided / speed) if speed else None

    waiting = []
    for c in cases:
        st = c.get("state") or workflow.state(c)
        if st not in workflow.OPEN_STATES:
            continue
        created = jalali.parse_jdate(created_at.get(c["smr"]))
        if created:
            waiting.append(((today - created).days, c["smr"], st, c.get("site", "")))
    waiting.sort(reverse=True)
    # registration in NBO -> NBO's result. Not "first seen by the app": that made a request waiting 230 days look
    # finished "in less than a day" (live 2026-10-03).
    spans = []
    for s, done in finished.items():
        created = jalali.parse_jdate(created_at.get(s))
        if done and created:
            spans.append(max(0, (done.astimezone().date() - created).days))
    return dict(states=dict(states), open=sum(states.get(k, 0) for k in workflow.OPEN_STATES), undecided=undecided,
                speed=speed, eta_days=eta_days, oldest=waiting[:10], avg_days_to_done=(sum(spans) / len(spans)) if spans else None,
                people=people.most_common(10), daily=[(d, dict(daily[d])) for d in day_keys])
