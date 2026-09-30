"""Write a run's decisions to an .xlsx with three tabs: results / manual queue / summary. Persian, right-to-left."""
import xlsxwriter

ACTION_FA = {"APPROVE": "تایید", "EDIT": "نیاز به اصلاح", "CANCEL": "لغو", "MANUAL": "بررسی دستی"}
HEAD = ["کد درخواست", "وب‌سایت", "دسته‌بندی", "تاریخ ایجاد", "تصمیم", "دلیل (کد NBO)", "توضیح"]


def _line(r):
    return [r["smr"], r["site"] or "", r["category"] or "", r["created_at"] or "", ACTION_FA.get(r["action"], r["action"]),
            "، ".join(r["reason_codes"]), " | ".join(r["notes"])]


def write_xlsx(path, results: list, dry_run: bool = True):
    wb = xlsxwriter.Workbook(str(path))
    head = wb.add_format({"bold": True, "bg_color": "#1f3a5f", "font_color": "#ffffff", "align": "center", "valign": "vcenter"})
    fmt = {"APPROVE": wb.add_format({"bg_color": "#e3f4e7"}), "EDIT": wb.add_format({"bg_color": "#fff3d6"}),
           "CANCEL": wb.add_format({"bg_color": "#fbe1e1"}), "MANUAL": wb.add_format({"bg_color": "#e6e9f2"})}

    def sheet(name, rows):
        ws = wb.add_worksheet(name)
        ws.right_to_left(); ws.freeze_panes(1, 0)
        ws.write_row(0, 0, HEAD, head)
        for i, r in enumerate(rows, 1):
            ws.write_row(i, 0, _line(r), fmt.get(r["action"]))
        for col, w in enumerate((16, 34, 26, 14, 16, 40, 60)):
            ws.set_column(col, col, w)
        ws.autofilter(0, 0, max(len(rows), 1), len(HEAD) - 1)

    sheet("نتایج", results)
    sheet("صف دستی", [r for r in results if r["action"] == "MANUAL"])
    ws = wb.add_worksheet("خلاصه")
    ws.right_to_left()
    ws.write_row(0, 0, ["نتیجه", "تعداد"], head)
    for i, a in enumerate(("APPROVE", "EDIT", "CANCEL", "MANUAL"), 1):
        ws.write_row(i, 0, [ACTION_FA[a], sum(1 for r in results if r["action"] == a)])
    ws.write_row(6, 0, ["مجموع", len(results)])
    if dry_run:
        ws.write(8, 0, "حالت آزمایشی: هیچ تغییری در NBO اعمال نشده است.")
    ws.set_column(0, 0, 40)
    wb.close()
