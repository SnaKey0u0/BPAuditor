"""開機自動啟動：寫入 HKCU 的 Run 機碼，只對打包後的 exe 有效。"""
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "BPAuditor"
TRAY_ARG = "--tray"


def supported() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def command(exe: str) -> str:
    return f'"{exe}" {TRAY_ARG}'


def apply(enabled: bool):
    """開啟時寫入目前 exe 路徑（exe 被搬動後，下次啟動會更新）；關閉時移除。"""
    if not supported():
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command(sys.executable))
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
