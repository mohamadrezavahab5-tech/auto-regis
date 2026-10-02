"""Write a run's decisions to an .xlsx with three tabs: results / manual queue / summary. Persian, right-to-left.

Every cell is written as plain text or number: a website field such as '=HYPERLINK(...)' typed by a merchant must never become
a live formula in a colleague's Excel."""
import xlsxwriter

from .texts import ACTION_FA, notes_fa, reasons_fa

HEAD = ["کد درخواست", "وب‌سایت", "دسته‌بندی", "تاریخ ایجاد", "تصمیم", "کد دلیل NBO", "شرح دلیل", "توضیح", "زمان بررسی (ثانیه)"]
WIDTHS = (16, 34, 24, 13, 15, 34, 34, 70, 12)


def _line(r):
    secs = round(r["duration_ms"] / 1000, 1) if r.get("duration_ms") is not None else ""
    return [r["smr"], r.get("site") or "", r.get("category") or "", r.get("created_at") or "", ACTION_FA.get(r["action"], r["action"]),
            "، ".join(r.get("reason_codes") or []), reasons_fa(r.get("reason_codes")), notes_fa(r.get("notes")), secs]


def write_xlsx(path, results: list, dry_run: bool = True, sources: dict = None):
    wb = xlsxwriter.Workbook(str(path), {"strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False})
    head = wb.add_format({"bold": True, "bg_color": "#0e3b43", "font_color": "#ffffff", "align": "center", "valign": "vcenter",
                          "font_name": "Tahoma"})
    base = {"font_name": "Tahoma", "valign": "top", "text_wrap": True}
    fmt = {"APPROVE": wb.add_format({**base, "bg_color": "#e3f4e7"}), "EDIT": wb.add_format({**base, "bg_color": "#fff3d6"}),
           "CANCEL": wb.add_format({**base, "bg_color": "#fbe1e1"}), "MANUAL": wb.add_format({**base, "bg_color": "#e8eaf6"})}

    def sheet(name, rows):
        ws = wb.add_worksheet(name)
        ws.right_to_left()
        ws.freeze_panes(1, 0)
        ws.write_row(0, 0, HEAD, head)
        for i, r in enumerate(rows, 1):
            line = _line(r)
            f = fmt.get(r["action"])
            for j, v in enumerate(line):
                if isinstance(v, (int, float)):
                    ws.write_number(i, j, v, f)
                else:
                    ws.write_string(i, j, str(v), f)
        for col, w in enumerate(WIDTHS):
            ws.set_column(col, col, w)
        ws.autofilter(0, 0, max(len(rows), 1), len(HEAD) - 1)

    sheet("نتایج", results)
    sheet("صف دستی", [r for r in results if r["action"] == "MANUAL"])
    ws = wb.add_worksheet("خلاصه")
    ws.right_to_left()
    ws.write_row(0, 0, ["نتیجه", "تعداد"], head)
    for i, a in enumerate(("APPROVE", "EDIT", "CANCEL", "MANUAL"), 1):
        ws.write_string(i, 0, ACTION_FA[a])
        ws.write_number(i, 1, sum(1 for r in results if r["action"] == a))
    ws.write_string(6, 0, "مجموع")
    ws.write_number(6, 1, len(results))
    if dry_run:
        ws.write_string(8, 0, "حالت Fake: هیچ تغییری در NBO اعمال نشده است.")
    if sources:
        ws.write_string(9, 0, "تکراری‌ها با هر دو منبع بررسی شد — NBO: %s تاییدشده، CRM: %s تاییدشده" % (sources.get("nbo_approved"), sources.get("crm_approved")))
    ws.set_column(0, 0, 48)
    ws.set_column(1, 1, 12)
    wb.close()
