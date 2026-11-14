"""單一實體：第二個實體啟動時通知已在執行的那個叫出視窗，然後自己結束。

用 QLocalServer（Windows 上是具名管道），名稱帶使用者名稱，不同 Windows 帳號互不影響。
協定只有「連得上」：任何連線都代表「請顯示視窗」，不傳資料，第二個實體立刻結束也不會掉訊息。
"""
import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    return f"LoL_auditor-{getpass.getuser()}"


def notify_running(name: str | None = None, timeout_ms: int = 500) -> bool:
    """已有實體在執行就請它顯示視窗並回傳 True。"""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.disconnectFromServer()
    return True


class SingleInstance(QObject):
    """第一個實體持有的伺服器；有人連線就發出 show_requested。"""
    show_requested = Signal()

    def __init__(self, name: str | None = None):
        super().__init__()
        self.name = name or server_name()
        self.server = QLocalServer(self)
        if not self.server.listen(self.name):
            QLocalServer.removeServer(self.name)  # 上次閃退留下的殘骸
            self.server.listen(self.name)
        self.server.newConnection.connect(self._on_connection)

    def _on_connection(self):
        while (sock := self.server.nextPendingConnection()) is not None:
            sock.disconnected.connect(sock.deleteLater)
            sock.disconnectFromServer()
        self.show_requested.emit()
