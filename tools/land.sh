#!/usr/bin/env bash
# 把 feature 分支合入 dev：檢查被刪的斷言 → 合併 → 跑 tools/check.sh → 提交 → 關 issue → 刪本地與遠端分支。
# 用法：tools/land.sh <branch> <issue#|-> "<合併標題>" ["<trailer 區塊，可多行>"]
# 例：  tools/land.sh fix/foo 34 "修好某某" "Co-Authored-By: ..."
# 刪除既有斷言時會停下；確認行為已由別的測試保護後，以 ALLOW_REMOVED_ASSERTS=1 重跑。
set -euo pipefail
[ $# -ge 3 ] || { sed -n 2,5p "$0"; exit 2; }
branch=$1 issue=$2 title=$3 trailers=${4:-}
cd "$(git rev-parse --show-toplevel)"
[ -z "$(git status --porcelain)" ] || { echo "工作目錄有未提交的變更，先處理再合入。"; exit 1; }

# 只看淨刪除：同一檔案刪掉的 assert 行比新增的多（改寫斷言是一刪一增，不算）。
removed=$(git diff "dev...$branch" -- tests | awk '
  /^\+\+\+ b\// { f = substr($0, 7) }
  /^-[[:space:]]*assert/ { r[f]++; lines[f] = lines[f] "\n    " $0 }
  /^\+[[:space:]]*assert/ { a[f]++ }
  END { for (k in r) if (r[k] > a[k] + 0) printf "%s：刪 %d 條、加 %d 條%s\n", k, r[k], a[k] + 0, lines[k] }')
if [ -n "$removed" ] && [ "${ALLOW_REMOVED_ASSERTS:-}" != 1 ]; then
  echo "這個分支在以下檔案淨刪除了斷言。確認每一條保護的行為已由別的測試接手，再以 ALLOW_REMOVED_ASSERTS=1 重跑："
  echo "$removed"
  exit 1
fi

msg="Merge $branch: $title"
[ "$issue" = - ] || msg="$msg (#$issue)"
[ -z "$trailers" ] || msg="$msg"$'\n\n'"$trailers"

git checkout -q dev
git merge -q --no-ff --no-commit "$branch" || { git merge --abort; echo "合併衝突，已還原。"; exit 1; }
tools/check.sh || { git merge --abort; echo "檢查沒過，已還原合併。"; exit 1; }
git commit -q -m "$msg"
git log --oneline -1

[ "$issue" = - ] || gh issue close "$issue"
git branch -d "$branch"
if git ls-remote --exit-code --heads origin "$branch" >/dev/null; then
  git push -q origin --delete "$branch"
fi
