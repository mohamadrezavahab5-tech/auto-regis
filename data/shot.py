import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

app = QApplication(sys.argv)
app.setLayoutDirection(Qt.RightToLeft)
app.setFont(QFont("Segoe UI", 10))
from autoreview.ui import MainWindow

w = MainWindow()
w.show()


def shot():
    w.grab().save(r"C:\AutoReview\data\ui_smoke.png")
    app.quit()


QTimer.singleShot(1200, shot)
app.exec()
print("ok")
