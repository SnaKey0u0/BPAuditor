# 打包成 dist\LoL_auditor.exe
# 打包前先從 PATH 移除 Anaconda/conda，否則 PyInstaller 會打包到 Anaconda 的舊版 icuuc.dll（ICU 58），
# 導致執行時 "DLL load failed while importing QtCore: 找不到指定的程序"。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PATH = ($env:PATH -split ";" | Where-Object { $_ -notmatch "conda" }) -join ";"
& .venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name LoL_auditor `
    --icon assets\icon.ico --add-data "assets;assets" `
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失敗" }
Write-Host "完成：dist\LoL_auditor.exe"
