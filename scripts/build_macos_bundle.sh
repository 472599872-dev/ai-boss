#!/usr/bin/env bash
set -euo pipefail

BASE_URL=""
SKIP_MANIFEST="0"
TARGET_ARCH=""
ARTIFACT_SUFFIX=""
CODESIGN_IDENTITY=""
ENTITLEMENTS_FILE=""
NOTARY_APPLE_ID=""
NOTARY_PASSWORD=""
NOTARY_TEAM_ID=""

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
    --target-arch)
      TARGET_ARCH="${2:-}"
      shift 2
      ;;
    --artifact-suffix)
      ARTIFACT_SUFFIX="${2:-}"
      shift 2
      ;;
    --codesign-identity)
      CODESIGN_IDENTITY="${2:-}"
      shift 2
      ;;
    --entitlements)
      ENTITLEMENTS_FILE="${2:-}"
      shift 2
      ;;
    --notary-apple-id)
      NOTARY_APPLE_ID="${2:-}"
      shift 2
      ;;
    --notary-password)
      NOTARY_PASSWORD="${2:-}"
      shift 2
      ;;
    --notary-team-id)
      NOTARY_TEAM_ID="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "$TARGET_ARCH" ]]; then
  case "$TARGET_ARCH" in
    arm64|x86_64|universal2)
      ;;
    *)
      echo "不支持的 --target-arch: ${TARGET_ARCH}（可选: arm64, x86_64, universal2）" >&2
      exit 1
      ;;
  esac
fi

if [[ -z "$ENTITLEMENTS_FILE" ]]; then
  ENTITLEMENTS_FILE="$ROOT_DIR/scripts/macos.entitlements"
fi

NOTARY_ANY_SET="0"
if [[ -n "$NOTARY_APPLE_ID" || -n "$NOTARY_PASSWORD" || -n "$NOTARY_TEAM_ID" ]]; then
  NOTARY_ANY_SET="1"
fi

if [[ -n "$CODESIGN_IDENTITY" && ! -f "$ENTITLEMENTS_FILE" ]]; then
  echo "entitlements 文件不存在: $ENTITLEMENTS_FILE" >&2
  exit 1
fi

if [[ "$NOTARY_ANY_SET" == "1" ]]; then
  if [[ -z "$CODESIGN_IDENTITY" ]]; then
    echo "启用 notarize 前必须提供 --codesign-identity" >&2
    exit 1
  fi
  if [[ -z "$NOTARY_APPLE_ID" || -z "$NOTARY_PASSWORD" || -z "$NOTARY_TEAM_ID" ]]; then
    echo "notarize 参数不完整，必须同时提供 --notary-apple-id/--notary-password/--notary-team-id" >&2
    exit 1
  fi
fi

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
PYINSTALLER_ARGS=(--noconfirm)
if [[ -n "$TARGET_ARCH" ]]; then
  PYINSTALLER_ARGS+=(--target-arch "$TARGET_ARCH")
fi
PYINSTALLER_ARGS+=(AIBossWorkbench.spec)
"$PYTHON" -m PyInstaller "${PYINSTALLER_ARGS[@]}"

APP_PATH="$ROOT_DIR/dist/AIBossWorkbench.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "未生成 macOS app: $APP_PATH" >&2
  exit 1
fi

ZIP_PATH="$ROOT_DIR/release/AIBossWorkbench-macOS-v${VERSION}${ARTIFACT_SUFFIX}.zip"
DMG_PATH="$ROOT_DIR/release/AIBossWorkbench-macOS-v${VERSION}${ARTIFACT_SUFFIX}.dmg"
rm -f "$ZIP_PATH" "$DMG_PATH" "$ROOT_DIR/release/latest-macos.json"

if [[ -n "$CODESIGN_IDENTITY" ]]; then
  step "代码签名 App Bundle"
  codesign --force --deep --strict --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS_FILE" \
    --sign "$CODESIGN_IDENTITY" \
    "$APP_PATH"
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
fi

step "生成 ZIP（notarize 输入）"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

if [[ "$NOTARY_ANY_SET" == "1" ]]; then
  step "公证 APP 压缩包"
  xcrun notarytool submit "$ZIP_PATH" \
    --apple-id "$NOTARY_APPLE_ID" \
    --password "$NOTARY_PASSWORD" \
    --team-id "$NOTARY_TEAM_ID" \
    --wait

  step "staple App Bundle"
  xcrun stapler staple "$APP_PATH"
  xcrun stapler validate "$APP_PATH"

  step "重新生成 ZIP（包含 stapled App）"
  ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"
fi

step "生成 DMG"
hdiutil create -volname "AI招聘工作台" -srcfolder "$APP_PATH" -ov -format UDZO "$DMG_PATH" >/dev/null

if [[ -n "$CODESIGN_IDENTITY" ]]; then
  step "代码签名 DMG"
  codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG_PATH"
  codesign --verify --verbose=2 "$DMG_PATH"
fi

if [[ "$NOTARY_ANY_SET" == "1" ]]; then
  step "公证 DMG"
  xcrun notarytool submit "$DMG_PATH" \
    --apple-id "$NOTARY_APPLE_ID" \
    --password "$NOTARY_PASSWORD" \
    --team-id "$NOTARY_TEAM_ID" \
    --wait

  step "staple DMG"
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"

  step "Gatekeeper 验证"
  spctl -a -vv -t open "$DMG_PATH"
fi

if [[ "$SKIP_MANIFEST" != "1" ]]; then
  FINAL_BASE_URL="$BASE_URL"
  if [[ -z "$FINAL_BASE_URL" ]]; then
    FINAL_BASE_URL="https://gitee.com/link-wei/ai-boss/raw/release-assets/releases/${VERSION}"
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
if [[ -n "$TARGET_ARCH" ]]; then
  echo "目标架构         : $TARGET_ARCH"
fi
echo "App Bundle       : $APP_PATH"
echo "ZIP              : $ZIP_PATH"
echo "DMG              : $DMG_PATH"
if [[ "$SKIP_MANIFEST" != "1" ]]; then
  echo "更新清单         : $ROOT_DIR/release/latest-macos.json"
fi
