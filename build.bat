@echo off
chcp 65001 >nul
rem 打包成 dist\BPAuditor.exe，可直接雙擊執行。
rem PATH 只保留 Windows 系統目錄：PATH 裡若有 Anaconda，PyInstaller 會打包到它的舊版 icuuc.dll（ICU 58），
rem 導致執行時 "DLL load failed while importing QtCore: 找不到指定的程序"。
setlocal
cd /d "%~dp0"
rem 啟用 repo 內的 git hooks（commit 前跑 ruff、merge 前跑 ruff + pytest）。這個設定存在本機 .git\config，clone 不會帶過來；
rem 要在改 PATH 之前做，才找得到 git。
if exist ".git" (
    where git >nul 2>&1 && git config core.hooksPath .githooks
)
rem 沒有 .venv（例如剛 clone 或資料夾被清掉）就先建立並安裝套件；要在改 PATH 之前做，才找得到 py。
if not exist ".venv\Scripts\python.exe" (
    echo 找不到 .venv，正在建立...
    py -3.11 -m venv .venv
)
if not exist ".venv\Scripts\pyinstaller.exe" (
    echo 正在安裝套件...
    .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist ".venv\Scripts\pyinstaller.exe" (
    if exist ".venv\Scripts\python.exe" (
        echo 套件安裝失敗，請看上面 pip 的錯誤訊息。若是檔案被鎖住，請確認沒有另一個 build.bat 或 pip 正在執行，再重跑一次。
    ) else (
        echo 建立 .venv 失敗：請確認已安裝 Python 3.11（py -3.11），再重新執行。
    )
    pause
    exit /b 1
)
set "PATH=%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem"
rem 暫存檔與 .spec 放到 %TEMP%，打包完刪除，專案資料夾只留 dist\BPAuditor.exe。
set "WORK=%TEMP%\BPAuditor-build"
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name BPAuditor ^
    --workpath "%WORK%" --specpath "%WORK%" ^
    --icon "%~dp0assets\icon.ico" --add-data "%~dp0assets;assets" ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf ^
    run.py
set "ERR=%errorlevel%"
rmdir /s /q "%WORK%" 2>nul
if not "%ERR%"=="0" (
    echo PyInstaller 失敗。若是檔案被鎖住，請先從系統匣按「結束」關掉 BPAuditor.exe。
) else (
    echo 完成：dist\BPAuditor.exe
)
pause
