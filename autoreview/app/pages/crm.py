"""CRM inside the app: the real Dynamics pages. CRM asks for a Windows (NTLM) sign-in; it is answered with the person's own
CRM login from this session, or the person types the password once."""
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFrame, QInputDialog, QLineEdit, QVBoxLayout, QWidget

from ... import crm_sync
from ..web import CRM_REGISTRATIONS, Page, crm_profile
from .nbo import browser_toolbar


class CrmPage(QWidget):
    title = "CRM"
    subtitle = "خود CRM، با ورود خودت — فهرست Merchant Registrations"

    def __init__(self, session, shell):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        self._loaded = False
        self._asked = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.view = QWebEngineView()
        page = Page(crm_profile(), self.view)
        page.authenticationRequired.connect(self._auth)
        self.view.setPage(page)
        top = browser_toolbar(self.view, CRM_REGISTRATIONS)
        top.addStretch(1)
        v.addLayout(top)
        frame = QFrame()
        frame.setProperty("card", "true")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        fl.addWidget(self.view)
        v.addWidget(frame, 1)

    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self.view.load(QUrl(CRM_REGISTRATIONS))

    def _auth(self, _url, auth):
        user = crm_sync.stored_username() or self.session.profile.get("username", "")
        pw = self.session.crm_password
        if not pw and not self._asked:
            self._asked = True
            pw, ok = QInputDialog.getText(self, "ورود به CRM", f"رمز CRM برای {user}:", QLineEdit.EchoMode.Password)
            if ok and pw:
                self.session.crm_password = pw
        if self.session.crm_password:
            auth.setUser(user)
            auth.setPassword(self.session.crm_password)
