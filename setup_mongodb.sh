#!/usr/bin/env bash
# ============================================================
# tFlex GenBI — MongoDB 一鍵安裝 + 資料匯入腳本 (macOS / Apple Silicon)
# 預期環境:已裝 Homebrew (Apple Silicon: /opt/homebrew/bin/brew)
# ============================================================
set -e

# 自動把 brew 加進 PATH (Apple Silicon)
if [ -x "/opt/homebrew/bin/brew" ] && ! command -v brew >/dev/null 2>&1; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  tFlex GenBI — MongoDB Setup"
echo "════════════════════════════════════════════════════════════"

# ---------------------------------------------------------
echo ""
echo "▶︎ Step 1/7 · 檢查 Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  echo "✘ brew 不在 PATH。請先安裝 Homebrew:https://brew.sh/"
  exit 1
fi
echo "✓ brew at $(which brew)"

# ---------------------------------------------------------
echo ""
echo "▶︎ Step 2/7 · 安裝 MongoDB Community"
if brew list mongodb-community >/dev/null 2>&1; then
  echo "✓ mongodb-community 已安裝,跳過"
else
  brew tap mongodb/brew
  brew install mongodb-community
fi

# ---------------------------------------------------------
echo ""
echo "▶︎ Step 3/7 · 啟動 mongod 服務"
brew services start mongodb-community 2>&1 | tail -1
echo "  等待 mongod 起來..."
sleep 4

# ---------------------------------------------------------
echo ""
echo "▶︎ Step 4/7 · 驗證 MongoDB 連線"
if ! command -v mongosh >/dev/null 2>&1; then
  echo "✘ mongosh 不在 PATH (brew 安裝後應該有,請重開 Terminal)"
  exit 1
fi
mongosh --quiet --eval "db.runCommand({ping:1})" || {
  echo "✘ MongoDB ping 失敗,請檢查 brew services list"
  exit 1
}
echo "✓ MongoDB 可連線 (localhost:27017)"

# ---------------------------------------------------------
echo ""
echo "▶︎ Step 5/7 · 確認 pymongo Python 套件"
if python3 -c "import pymongo" 2>/dev/null; then
  echo "✓ pymongo 已就緒"
else
  echo "  安裝 pymongo..."
  pip3 install --quiet --break-system-packages pymongo
fi

# ---------------------------------------------------------
echo ""
echo "▶︎ Step 6/7 · 匯入 tFlex 兩張 CSV 到 MongoDB"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
python3 import_tflex_to_mongodb.py

# ---------------------------------------------------------
echo ""
echo "▶︎ Step 7/7 · 資料正確性驗證"
mongosh tflex_demo --quiet --eval '
  const apps_total = db.tflex_applications.countDocuments({});
  const apps_company = db.tflex_company_hc.countDocuments({});
  const completed = db.tflex_applications.countDocuments({review_status:"Y"});
  const returned = db.tflex_applications.countDocuments({review_status:"Y", review_result:"N"});
  const in_progress = db.tflex_applications.countDocuments({review_status:"N"});
  const ai = db.tflex_applications.countDocuments({review_status:"Y", review_mechanism:"AI"});

  print("─ 預期 vs 實際 ───────────────────────────");
  print("applications  expected 147,526 · actual " + apps_total.toLocaleString());
  print("companies     expected      15 · actual " + apps_company);
  print("completed     expected 135,276 · actual " + completed.toLocaleString());
  print("returned      expected   4,963 · actual " + returned.toLocaleString());
  print("in_progress   expected  12,250 · actual " + in_progress.toLocaleString());
  print("AI reviews    (≈ 43% of completed) actual " + ai.toLocaleString());
'

echo ""
echo "════════════════════════════════════════════════════════════"
echo "🎉 完成!現在可以啟動 GenBI:"
echo "    streamlit run app.py"
echo "════════════════════════════════════════════════════════════"
