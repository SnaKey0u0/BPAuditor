# 血壓稽查器（台服 ARAM）

在組隊房間裡複製「`某某 #標籤 進入組隊房間`」訊息，程式就會自動查詢每位房間成員最近 N 場（預設 10）的勝率、KDA、等級，並標示**疑似多排**與**與你相遇**（上一場的隊友/敵人）。資料來自 Riot 官方 API（見 `docs/adr/0001`），術語見 `GLOSSARY.md`。

## 使用

1. 執行 `BPAuditor.exe`。
2. 第一次啟動會開設定頁。到 <https://developer.riotgames.com> 用 Riot 帳號登入，複製 **API Key**，程式會自動存入。再填上你自己的 Riot ID（`名稱#標籤`），用來標示「與你相遇」。改名後程式會自動更新。
3. 在遊戲聊天室選取並複製房間訊息，程式會自動稽查並閃爍工作列。名單沒變就不重查，多了新人只查新的人。
4. 想看某人某隻英雄的表現：在卡片的「查詢特定英雄」輸入中文名（會自動補完）。勝敗長條贏的朝上、輸的朝下，滑鼠停在長條上可看那場的英雄與 KDA。

視窗的 ✕ 會縮到系統匣，剪貼簿監看繼續運作；要結束程式請在系統匣圖示按右鍵 →「結束」。設定頁可以開啟「開機時啟動」（只對 exe 有效）。

設定存在 `%APPDATA%\BPAuditor\config.json`，英雄頭像存在同目錄的 `icons\`。**程式不內建任何 API Key**。

### 更新開發 Key（每 24 小時）
右下角會顯示 Key 剩幾小時。過期時按「更新 API Key」開啟 developer.riotgames.com，按 **Regenerate API Key** 再按複製。程式隨時監看剪貼簿，抓到新 Key 就會存檔並驗證。

> Riot 沒有提供換 Key 的 API，而自動登入網站需要你的帳密並繞過人機驗證、違反開發者條款，所以這一步刻意保留為手動按一下。

## 開發

```powershell
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest            # 測試
.venv\Scripts\python run.py               # 執行
.venv\Scripts\python tools\make_icon.py   # 重新產生 assets\icon.ico 與 icon.png
build.bat                                 # 打包到 dist\，可直接雙擊（PATH 只留系統目錄，避免打包到 Anaconda 的 icuuc.dll）
```

| 模組 | 職責 |
|---|---|
| `parsing.py` | 房間訊息 → Riot ID 清單；房間名單變化（不變/多人/少人/換人） |
| `riot.py` | Riot API 客戶端 + 開發 Key 限速（20/秒、100/2 分） |
| `analysis.py` | 純函式：近期戰績、指定英雄戰績、疑似多排、相遇 |
| `audit.py` | 一次稽查的並行流程，查完一人回報一人；偵測本人改名 |
| `champions.py` | Data Dragon 英雄繁中名稱與頭像 |
| `autostart.py` | 開機自動啟動（HKCU Run 機碼） |
| `gui.py` | PySide6 介面 |
