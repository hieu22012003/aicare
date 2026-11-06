"""One bounded video operation at a time; no Qt widgets in inference threads."""
from PyQt5.QtCore import QThread, pyqtSignal


class FallVideoTask(QThread):
    result_ready=pyqtSignal(object)
    failed=pyqtSignal(str)

    def __init__(self,function,parent=None):
        super().__init__(parent)
        self.function=function
        self.cancelled=False

    def run(self):
        try:
            value=self.function()
            if self.cancelled:
                if value.get('runner'):value['runner'].close()
            else:self.result_ready.emit(value)
        except Exception as error:
            if not self.cancelled:self.failed.emit(str(error))
