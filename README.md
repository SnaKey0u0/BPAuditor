# 血壓稽查器（台服 ARAM）

在組隊房間裡複製「`某某 #標籤 進入組隊房間`」訊息，程式就會自動查詢每位房間成員（含你自己）最近 N 場（預設 5）的勝率、KDA、KD、等級與牌位（單雙／彈性），並標示**可能多排**與**與你相遇**（上一場的隊友/敵人）。進入遊戲後還能**查對面**：左右兩隊並列，並顯示每人本場英雄的熟練度。資料來自 Riot 官方 API（見 `docs/adr/0001`），術語見 `GLOSSARY.md`。

## 使用

1. 執行 `BPAuditor.exe`。
2. 第一次啟動會開設定頁。到 <https://developer.riotgames.com> 用 Riot 帳號登入，複製你的 **Personal API Key**，程式會自動加入 Key 清單。再填上你自己的 Riot ID（`名稱#標籤`），用來標示「與你相遇」。改名後程式會自動更新。
3. 在遊戲聊天室選取並複製房間訊息，程式會自動稽查並閃爍工作列。名單沒變就不重查，多了新人只查新的人。
4. 近期戰績的每一場是一個英雄頭像方塊（由新到舊，底部綠條＝贏、紅條＝輸），滑鼠停在方塊上可看那場的英雄、KDA 與模式。平均 KDA 低於 3、KD 低於 1 顯示紅色，否則綠色。點玩家名稱會在 op.gg 開啟他的個人頁，看更詳細的統計。
5. 查對面：稽查完成 90 秒後，程式每 20 秒用 Riot 官方觀戰 API 查你是否已開局（載入畫面開始才查得到，不讀遊戲客戶端），查到就自動切成左右兩隊。也可以按「查對面」手動查；手動查過就不再自動查。

視窗的 ✕ 會縮到系統匣，剪貼簿監看繼續運作；要結束程式請在系統匣圖示按右鍵 →「結束」。設定頁可以開啟「開機時啟動」（只對 exe 有效）。

設定存在 `%APPDATA%\BPAuditor\config.json`，英雄頭像存在同目錄的 `icons\`。**程式不內建任何 API Key**。

### Key 清單
設定頁可以存好幾組 Personal API Key（#1、#2…），每列顯示遮罩過的 Key 與狀態（驗證中／新 Key 生效中／✓ 可用／失效），按 ✕ 刪除。所有請求一律用清單中**第一組可用的 Key**。新增 Key：在輸入框貼上後按 Enter，或按「取得新 Key」開啟 developer.riotgames.com 後複製——程式隨時監看剪貼簿，抓到新 Key 就會加入清單並驗證（不會取代原本的 Key）。回 401/403 的 Key 標成「失效，請重新產生」，不會自動刪除；全部失效時畫面上方會顯示提示。

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
| `riot.py` | Riot API 客戶端 + Key 限速（20/秒、100/2 分） |
| `keys.py` | Key 清單：多組 Personal Key 的新增／刪除、驗證狀態、選出第一組可用的 Key |
| `analysis.py` | 純函式：近期戰績、牌位、英雄熟練度、可能多排、相遇、拆開進行中對局的兩隊 |
| `audit.py` | 一次稽查的並行流程，查完一人回報一人；偵測本人改名 |
| `champions.py` | Data Dragon 英雄繁中名稱與頭像 |
| `autostart.py` | 開機自動啟動（HKCU Run 機碼） |
| `gui.py` | PySide6 介面 |
