from pathlib import Path

R = Path(r"C:\AutoReview")


def sub(rel, old, new):
    p = R / rel
    t = p.read_text(encoding="utf-8")
    assert old in t, (rel, old[:70])
    p.write_text(t.replace(old, new, 1), encoding="utf-8")


# ---------- crm_sync: domain prefix (same rule as the team's other CRM tool), login check, no repeated bad attempts ----------
sub("autoreview/crm_sync.py", '''FORMATTED = "@OData.Community.Display.V1.FormattedValue"
''', '''FORMATTED = "@OData.Community.Display.V1.FormattedValue"
DOMAIN = "SNAPP"                     # the NTLM domain seen from crm.snapppay.ir (the team's other tool adds it the same way)


class CrmAuthError(RuntimeError):
    """The CRM refused the stored login (HTTP 401)."""


def normalize_username(username: str) -> str:
    """'ali' -> 'SNAPP\\\\ali'; 'SNAPP\\\\ali' and 'ali@domain' are left as typed."""
    u = (username or "").strip()
    return u if ("\\\\" in u or "@" in u) else f"{DOMAIN}\\\\{u}"
''')
sub("autoreview/crm_sync.py", '''    r = _ps([str(_script("crm-save-cred.ps1")), "-OutFile", str(cred_file())], stdin=f"{username}\\n{password}\\n", timeout=60)''',
    '''    r = _ps([str(_script("crm-save-cred.ps1")), "-OutFile", str(cred_file())], stdin=f"{normalize_username(username)}\\n{password}\\n", timeout=60)''')
sub("autoreview/crm_sync.py", '''    if r.returncode != 0:
        raise RuntimeError("CRM read failed: " + (r.stderr.strip() or r.stdout.strip())[:300])
    return json.loads(r.stdout)''', '''    if r.returncode != 0:
        err = (r.stderr.strip() or r.stdout.strip())
        if "401" in err or "Unauthorized" in err:
            forget_credentials()          # never resend a refused password: repeated failures can lock the person's domain account
            raise CrmAuthError("CRM نام کاربری یا رمز را رد کرد. برای جلوگیری از قفل شدن حساب، ورود ذخیره‌شده پاک شد؛ "
                               "یک‌بار دیگر و با دقت «ورود به CRM» را بزن.")
        raise RuntimeError("CRM read failed: " + err[:300])
    return json.loads(r.stdout)''')
sub("autoreview/crm_sync.py", '''def get_all(path: str, runner=None, max_pages=200):''', '''def whoami(runner=None) -> dict:
    """Cheapest authenticated call: proves the stored login works (no data is read)."""
    return get("WhoAmI", runner)


def get_all(path: str, runner=None, max_pages=200):''')

# ---------- UI: check the login right after it is typed ----------
sub("autoreview/ui.py", '''        form.addRow(QLabel("این مشخصات فقط روی همین کامپیوتر و با رمزنگاری ویندوز ذخیره می‌شود.\\nاپ فقط از CRM می‌خواند و چیزی در آن نمی‌نویسد."))''',
    '''        form.addRow(QLabel("این مشخصات فقط روی همین کامپیوتر و با رمزنگاری ویندوز ذخیره می‌شود.\\nاپ فقط از CRM می‌خواند و چیزی در آن نمی‌نویسد.\\nفقط نام کاربری را بنویس؛ «SNAPP\\\\» خودکار اضافه می‌شود."))''')
sub("autoreview/ui.py", '''            try:
                crm_sync.save_credentials(d.user.text().strip(), d.pw.text())
                self.crm_label.setText("CRM: ورود ذخیره شد")
            except Exception as e:
                QMessageBox.critical(self, "خطا", str(e))''', '''            try:
                crm_sync.save_credentials(d.user.text().strip(), d.pw.text())
            except Exception as e:
                QMessageBox.critical(self, "خطا", str(e)); return
            self.crm_label.setText("CRM: در حال بررسی ورود…")

            def check():
                try:
                    crm_sync.whoami()
                    self.bridge.crm_done.emit("CRM: ورود تایید شد ✓ — حالا «دریافت خودکار از CRM» را بزن", True)
                except Exception as e:
                    self.bridge.crm_done.emit(f"CRM: {e}", False)
            threading.Thread(target=check, daemon=True).start()''')

# ---------- tests ----------
t = (R / "tests" / "test_crm_sync.py").read_text(encoding="utf-8")
t += '''

def test_username_gets_the_domain_unless_it_already_has_one():
    assert c.normalize_username("ali") == "SNAPP\\\\ali"
    assert c.normalize_username(" ali ") == "SNAPP\\\\ali"
    assert c.normalize_username("SNAPP\\\\ali") == "SNAPP\\\\ali"
    assert c.normalize_username("ali@snapp.ir") == "ali@snapp.ir"


def test_a_401_removes_the_stored_login_and_is_reported_clearly(tmp_path, monkeypatch):
    class R:
        returncode, stdout, stderr = 1, "", "The remote server returned an error: (401) Unauthorized."
    cred = tmp_path / "cred.xml"; cred.write_text("x")
    monkeypatch.setattr(c, "cred_file", lambda: cred)
    monkeypatch.setattr(c, "_ps", lambda *a, **k: R())
    with pytest.raises(c.CrmAuthError):
        c.whoami()
    assert not cred.exists()                      # a refused password is never sent again
'''
(R / "tests" / "test_crm_sync.py").write_text(t, encoding="utf-8")
print("patched")
