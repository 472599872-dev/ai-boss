#!/usr/bin/env bash
set -euo pipefail

BASE_URL=""
SKIP_MANIFEST="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="${2:-}"
      shift 2
      ;;
    --skip-manifest)
      SKIP_MANIFEST="1"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

step() {
  printf '\n==> %s\n' "$1"
}

if [[ ! -f app_version.txt ]]; then
  echo "未找到 app_version.txt" >&2
  exit 1
fi
VERSION="$(tr -d '\r\n' < app_version.txt)"
if [[ -z "$VERSION" ]]; then
  echo "app_version.txt 为空" >&2
  exit 1
fi

step "准备虚拟环境"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
PYTHON=".venv/bin/python"

step "安装依赖"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt pyinstaller

step "清理旧产物"
rm -rf build dist
mkdir -p release

step "执行 PyInstaller"
"$PYTHON" -m PyInstaller --noconfirm AIBossWorkbench.spec

APP_PATH="$ROOT_DIR/dist/AIBossWorkbench.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "未生成 macOS app: $APP_PATH" >&2
  exit 1
fi

ZIP_PATH="$ROOT_DIR/release/AIBossWorkbench-macOS-v${VERSION}.zip"
DMG_PATH="$ROOT_DIR/release/AIBossWorkbench-macOS-v${VERSION}.dmg"
rm -f "$ZIP_PATH" "$DMG_PATH" "$ROOT_DIR/release/latest-macos.json"

step "生成 ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

step "生成 DMG"
hdiutil create -volname "AI招聘工作台" -srcfolder "$APP_PATH" -ov -format UDZO "$DMG_PATH" >/dev/null

if [[ "$SKIP_MANIFEST" != "1" ]]; then
  FINAL_BASE_URL="$BASE_URL"
  if [[ -z "$FINAL_BASE_URL" ]]; then
    FINAL_BASE_URL="https://your-domain/releases/${VERSION}"
  fi
  step "生成 latest-macos.json"
  "$PYTHON" scripts/generate_macos_update_manifest.py \
    --base-url "$FINAL_BASE_URL" \
    --zip "$ZIP_PATH" \
    --dmg "$DMG_PATH" \
    --out "release/latest-macos.json"
fi

step "打包完成"
echo "版本号           : $VERSION"
echo "App Bundle       : $APP_PATH"
echo "ZIP              : $ZIP_PATH"
echo "DMG              : $DMG_PATH"
if [[ "$SKIP_MANIFEST" != "1" ]]; then
  echo "更新清单         : $ROOT_DIR/release/latest-macos.json"
fi
