import uuid

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from bp_auditor.single import SingleInstance, notify_running


def test_second_instance_asks_first_to_show():
    _app = QCoreApplication.instance() or QCoreApplication([])  # 留住參照，事件迴圈才跑得起來
    name = f"lol-auditor-test-{uuid.uuid4().hex}"
    assert notify_running(name, 200) is False  # 沒人在跑
    first = SingleInstance(name)
    shown = []
    first.show_requested.connect(lambda: shown.append(True))
    assert notify_running(name) is True
    loop = QEventLoop(); QTimer.singleShot(300, loop.quit); loop.exec()
    assert shown == [True]
