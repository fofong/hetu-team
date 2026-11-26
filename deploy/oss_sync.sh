#!/bin/bash
# Hetu Team 开源同步：把脱敏后的公开文件树同步到 staging 仓库并推送 GitHub（MIT）
# 用法: bash deploy/oss_sync.sh ["commit message"]
# 硬性排除: poc/(含AGPL代码)、data/、logs/、.venv、账本与内部部署记录（含内网拓扑/凭据）
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$HOME/code/hetu-team-oss"
REPO="https://github.com/fofong/hetu-team.git"

mkdir -p "$STAGE"
cd "$STAGE"
[ -d .git ] || { git init -q -b main; git remote add origin "$REPO"; }

# 白名单同步（先清后拷，保证删除也同步）
for d in src tests client deploy docs packaging; do rm -rf "${STAGE:?}/$d"; done
rsync -a --exclude __pycache__ --exclude .pytest_cache "$SRC/src" "$STAGE/"
rsync -a "$SRC/tests" "$STAGE/"
rsync -a "$SRC/client" "$STAGE/"
rsync -a --exclude __pycache__ "$SRC/deploy" "$STAGE/"
mkdir -p "$STAGE/docs"
rsync -a "$SRC/docs/research" "$STAGE/docs/"
mkdir -p "$STAGE/packaging/macos"
cp "$SRC/packaging/macos/build_macos_app.sh" "$SRC/packaging/macos/gui.py" "$SRC/packaging/macos/AppIcon.icns" "$STAGE/packaging/macos/" 2>/dev/null || true
cp "$SRC/pytest.ini" "$SRC/conftest.py" "$STAGE/" 2>/dev/null || true
[ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$STAGE/"
[ -f "$SRC/LICENSE" ] && cp "$SRC/LICENSE" "$STAGE/"
[ -f "$SRC/.gitignore" ] && cp "$SRC/.gitignore" "$STAGE/"

# 兜底敏感扫描（命中即中止，不推送）
if grep -rniE "hahaxiao|BEGIN.*PRIVATE KEY|gho_|sk-[A-Za-z0-9]{20}" "$STAGE" --include="*.py" --include="*.md" --include="*.html" --include="*.sh" --exclude=oss_sync.sh 2>/dev/null; then
  echo "ABORT: 敏感信息命中，禁止推送"; exit 1
fi

git add -A
if git diff --cached --quiet; then echo "no changes"; exit 0; fi
git -c user.name=Hetu -c user.email=hongyun@hongyun.dev commit -q -m "${1:-chore: daily OSS sync $(date +%F)}"
git push -q origin main
echo "pushed: $(git log --oneline -1)"
