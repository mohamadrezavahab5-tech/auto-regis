"""One request's whole story on one sheet: the decision, why (every check in plain Persian with what it found), the
evidence, the two teams' verdicts, NBO's status and the dated history. Owner 2026-10-03: in the meeting "open this one,
why did it decide that?" took a search through several pages; now any page - and the search box in the top bar - opens
this same sheet."""
import html

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ... import activity, execution, jalali, reference, store, workflow
from ...texts import ACTION_FA, notes_fa, reasons_fa, step_fa
from ..theme import C
from ..widgets import Card, action_pill, button, label, ltr, toast
from .common import nbo_status_fa, related_card

STEP_LOOK = {"PASS": ("✓", "approve"), "FAIL": ("✗", "cancel"), "UNKNOWN": ("؟", "manual"), "BLOCKED": ("!", "warn")}


def checks_card(result):
    """What the engine checked, in order, and what each check found."""
    card = Card()
    card.lay.addWidget(label("چه چیزهایی چک شد", "h3"))
    steps = (result or {}).get("trace") or []
    if not steps:
        card.lay.addWidget(label("این درخواست هنوز بررسی نشده است.", "caption"))
        return card
    for step in steps:
        kind, text = step_fa(step)
        sym, color = STEP_LOOK.get(kind, ("•", "text2"))
        card.lay.addWidget(label(f"<span style='color:{C[color]}; font-weight:700'>{sym}</span>&nbsp; {html.escape(text)}", wrap=True))
    card.lay.addWidget(label("بررسی به ترتیب قانون‌ها جلو می‌رود و با اولین قانونی که رد شود یا نامشخص بماند، همان‌جا تصمیم می‌گیرد.",
                             "caption", wrap=True))
    return card


def _verdict_line(title, v, needed=True):
    if not v:
        return f"<b>{title}:</b> " + ("هنوز نظری ثبت نشده" if needed else "لازم نیست")
    who = "موتور (خودکار)" if v.get("source") == "engine" else html.escape(v.get("actor") or "—")
    text = f"<b>{title}:</b> {ACTION_FA.get(v.get('action'), v.get('action'))} — {who}"
    if v.get("at"):
        text += f" <span style='color:{C['text3']}'>({jalali.jdatetime(v['at'])})</span>"
    reasons = v.get("reasons") or ([v["reason"]] if v.get("reason") else [])
    if reasons:
        text += "<br>دلیل NBO: " + html.escape(reasons_fa(reasons))
    if v.get("note"):
        text += f"<br><span style='color:{C['text2']}'>{html.escape(str(v['note']))}</span>"
    return text


EVENT_FA = {"WORKFLOW_IMPORTED": "وارد صف اپ شد", "WORKFLOW_ENGINE_VERDICT": "نتیجه‌ی موتور به‌عنوان نظر Online ثبت شد",
            "WORKFLOW_SOURCE_CHANGED": "اطلاعات درخواست در NBO عوض شد؛ نظرها از نو شروع شد",
            "WORKFLOW_SOURCE_REMOVED": "از خروجی NBO بیرون رفت", "MANUAL_DONE": "بررسی دستی انجام شد",
            "MANUAL_REOPENED": "دوباره به صف دستی برگشت", "DUPLICATE_FOUND": "تکراری پیدا شد"}


def _timeline(db, smr):
    """Everything that happened to one request, oldest first, each thing once and in Persian: engine reviews, verdicts,
    NBO status changes, what the app did in NBO and who did it. -> [(iso time, Persian sentence)]
    One source - the audit table, which records all of these; reading the workflow events and the execution table as
    well showed every step two or three times, some under their internal names (owner 2026-10-03)."""
    modes = {"manual": "دستی", "automatic": "خودکار"}
    out = []
    for e in store.history(db, smr):
        kind, d = e["kind"], e.get("detail") or {}
        who = d.get("actor") or d.get("user") or ""
        if kind == "DECISION":
            text = f"بررسی موتور: {ACTION_FA.get(e['action'], e['action'])}" + (f" — {reasons_fa(e['codes'])}" if e["codes"] else "")
        elif kind in ("WORKFLOW_SUGGESTED", "NBO_PREVIEW", "NBO_REHEARSED"):
            continue                                        # the review itself is listed; "ready" and rehearsals are not events
        elif kind == "WORKFLOW_HUMAN_DECISION":
            team = {"online": "Online", "instore": "Instore"}.get(d.get("team"), d.get("team") or "")
            text = f"نظر تیم {team}: {ACTION_FA.get(d.get('action'), d.get('action') or '')}" + (f" — {who}" if who else "")
        elif kind == "WORKFLOW_NBO_STATUS":
            text = "وضعیت در NBO شد: " + activity.NBO_FA.get(d.get("status"), nbo_status_fa(d.get("status")))
        elif kind == "NBO_SENDING":
            text = "ارسال به NBO شروع شد" + (f" — {who}" if who else "")
        elif kind == "NBO_SENT":
            how = " — ".join(x for x in (who, modes.get(d.get("mode"), "")) if x)
            text = "در NBO ثبت شد: " + str(d.get("detail") or "") + (f" ({how})" if how else "")
        elif kind.startswith("NBO_"):
            text = execution.LABELS.get(kind[4:], kind) + (f" — {d.get('detail')}" if d.get("detail") else "")
        else:
            text = EVENT_FA.get(kind, kind) + (f" — {who}" if who and who != "AutoReview" else "")
        if e["ts"] and (not out or out[-1][1] != text or out[-1][0][:16] != e["ts"][:16]):
            out.append((e["ts"], text))
    return out


def case_widget(session, shell, smr, on_close=None):
    """-> the sheet for `smr` (a QWidget), or None when the app has never seen this request."""
    from .results import evidence_card
    db = session.read_db(300)
    try:
        case = workflow.get(db, smr)
        result = store.latest_result(db, smr)
        ref = db.execute("SELECT status, site, category, created_at, brand_fa, has_instore FROM ref_nbo WHERE smr = ?", (smr,)).fetchone()
        timeline = _timeline(db, smr)
        receipt = next((r for r in execution.records(db) if r['smr'] == smr and
                        (not case or r['revision'] == case['revision'] or r['state'] in ('SENDING', 'UNCERTAIN'))), None)
        rel = reference.related(db, smr)
    finally:
        db.close()
    if not case and not result and not ref:
        return None
    site = (case or {}).get("site") or (result or {}).get("site") or (ref[1] if ref else "") or ""
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    v = QVBoxLayout(w)
    v.setContentsMargins(6, 6, 6, 12)
    v.setSpacing(12)

    head = Card()
    top = QHBoxLayout()
    top.addWidget(label(ltr(smr), "h2", selectable=True))
    top.addStretch(1)
    state = workflow.state(case) if case else None
    action = (execution.target(case) or (None,))[0] if case else None
    shown = action or (result or {}).get("action")
    if shown:
        top.addWidget(action_pill(shown, ACTION_FA.get(shown, shown)))
    head.lay.addLayout(top)
    bits = [ltr(html.escape(site))] if site else []
    if ref:
        bits.append("Online + Instore" if str(ref[5]).lower() == "true" else "Online")
        if ref[2]:
            bits.append(html.escape(str(ref[2])))
        if ref[3]:
            bits.append("ثبت در NBO: " + html.escape(str(ref[3])))
    head.lay.addWidget(label("  •  ".join(bits), "muted", wrap=True, selectable=True))
    if state:
        head.lay.addWidget(label(f"<b>وضعیت در اپ:</b> {html.escape(workflow.STATES[state])}", wrap=True))
    if case:
        progress, next_step = activity.status(case, receipt)
        head.lay.addWidget(label('<b>' + html.escape(progress) + '</b><br>قدم بعدی: ' + html.escape(next_step), wrap=True))
    if ref:
        head.lay.addWidget(label(f"<b>وضعیت در NBO:</b> {html.escape(nbo_status_fa(ref[0]))}", wrap=True))
    btns = QHBoxLayout()
    b_copy = button("کپی کد", None, "copy")
    b_copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(smr), toast(shell, "کد کپی شد")))
    btns.addWidget(b_copy)
    if site:
        b_site = button("باز کردن سایت", None, "external")
        url = site if "://" in site else "https://" + site
        b_site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        btns.addWidget(b_site)
    b_nbo = button("دیدن در NBO", None, "nbo")
    b_nbo.clicked.connect(lambda: ((on_close() if on_close else None), shell.open_in_nbo(smr)))
    btns.addWidget(b_nbo)
    btns.addStretch(1)
    head.lay.addLayout(btns)
    v.addWidget(head)

    why = Card()
    why.lay.addWidget(label("تصمیم و دلیلش", "h3"))
    if result:
        line = f"<b>نتیجه‌ی بررسی موتور:</b> {ACTION_FA.get(result['action'], result['action'])}"
        if result.get("reason_codes"):
            line += " — " + html.escape(reasons_fa(result["reason_codes"]))
        line += f" <span style='color:{C['text3']}'>({jalali.jdatetime(result['decided_at'])})</span>"
        why.lay.addWidget(label(line, wrap=True))
        if result.get("notes"):
            why.lay.addWidget(label(html.escape(notes_fa(result["notes"])), "muted", wrap=True))
    else:
        why.lay.addWidget(label("موتور هنوز این درخواست را بررسی نکرده است.", "muted"))
    if case:
        both = case.get("channel") == "both"
        why.lay.addWidget(label(_verdict_line("نظر تیم Online", case.get("online")), wrap=True))
        why.lay.addWidget(label(_verdict_line("نظر تیم Instore", case.get("instore"), needed=both), wrap=True))
    v.addWidget(why)

    v.addWidget(checks_card(result))
    v.addWidget(evidence_card((result or {}).get("evidence") or {}))
    related = related_card(rel)
    if related:
        v.addWidget(related)

    hcard = Card()
    hcard.lay.addWidget(label("چه اتفاق‌هایی افتاد", "h3"))
    for when, text in timeline:
        hcard.lay.addWidget(label(f"<span style='color:{C['text3']}'>{jalali.jdatetime(when)}</span>&nbsp;&nbsp;{html.escape(text)}", wrap=True))
    if not timeline:
        hcard.lay.addWidget(label("اتفاقی ثبت نشده.", "caption"))
    v.addWidget(hcard)
    v.addStretch(1)
    return w


def open_case(shell, smr):
    """The sheet in its own window (stays open next to the app; one per request)."""
    open_ = getattr(shell, "_case_windows", None)
    if open_ is None:
        open_ = shell._case_windows = {}
    old = open_.get(smr)
    if old is not None:
        old.raise_()
        old.activateWindow()
        return old
    dlg = QDialog(shell)
    dlg.setWindowTitle(f"پرونده‌ی {smr}")
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.resize(720, 820)
    body = case_widget(shell.session, shell, smr, on_close=dlg.close)
    if body is None:
        dlg.deleteLater()
        return None
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(10, 10, 10, 10)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(body)
    lay.addWidget(area)
    open_[smr] = dlg
    dlg.destroyed.connect(lambda *_: open_.pop(smr, None))
    dlg.show()
    return dlg


def find_requests(session, text, limit=12):
    """Requests matching what a person typed: a request ID (with or without 'SMR-'), or part of a website / shop name.
    -> [(smr, site, status)], exact ID first."""
    q = str(text or "").strip()
    if not q:
        return []
    db = session.read_db(300)
    try:
        digits = q.upper().replace("SMR-", "").replace("SMR", "").strip()
        if digits.isdigit():
            exact = db.execute("SELECT smr, site, status FROM ref_nbo WHERE smr = ?", ("SMR-" + digits,)).fetchall()
            if exact:
                return exact
            return db.execute("SELECT smr, site, status FROM ref_nbo WHERE smr LIKE ? ORDER BY created_at DESC LIMIT ?",
                              (f"%{digits}%", limit)).fetchall()
        like = f"%{q.lower()}%"
        return db.execute("SELECT smr, site, status FROM ref_nbo WHERE lower(site) LIKE ? OR brand_fa LIKE ? "
                          "ORDER BY created_at DESC LIMIT ?", (like, f"%{q}%", limit)).fetchall()
    finally:
        db.close()
