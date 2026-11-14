@echo off
chcp 65001 >nul
rem 打包成 dist\BPAuditor.exe，可直接雙擊執行。
rem PATH 只保留 Windows 系統目錄：PATH 裡若有 Anaconda，PyInstaller 會打包到它的舊版 icuuc.dll（ICU 58），
rem 導致執行時 "DLL load failed while importing QtCore: 找不到指定的程序"。
setlocal
cd /d "%~dp0"
set "PATH=%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem"
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name BPAuditor ^
    --icon assets\icon.ico --add-data "assets;assets" ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf ^
    run.py
if errorlevel 1 (
    echo PyInstaller 失敗。若是檔案被鎖住，請先從系統匣按「結束」關掉 BPAuditor.exe。
) else (
    echo 完成：dist\BPAuditor.exe
)
pause
