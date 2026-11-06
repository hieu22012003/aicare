import sys
import os
sys.path.insert(0, ".")
try:
    import mediapipe
except Exception:
    pass
from PyQt5.QtWidgets import QApplication, QPushButton
from apps.desktop.theme import LIGHT_THEME_STYLESHEET

app = QApplication([])
app.setStyleSheet(LIGHT_THEME_STYLESHEET)
btn = QPushButton("Test")
print("btn.style():", btn.style())
print("style class name:", btn.style().metaObject().className())
