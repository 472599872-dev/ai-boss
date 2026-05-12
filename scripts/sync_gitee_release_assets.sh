#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/sync_gitee_release_assets.sh --version <version> --source-dir <artifact-release-dir> [options]

Required:
  --version <version>         Release version, e.g. 1.0.18
  --source-dir <dir>          Directory containing downloaded GitHub artifact files

Optional:
  --repo-dir <dir>            Local checkout directory for the Gitee release-assets branch
                              Default: /tmp/ai-boss-release-assets
  --repo-url <url>            Gitee repository SSH URL
                              Default: git@gitee.com:link-wei/ai-boss.git
  --branch <name>             Release branch name
                              Default: release-assets
  --remote <name>             Remote name inside repo-dir
                              Default: origin
  --message <text>            Commit message
                              Default: release: sync v<version>
  --no-push                   Do not push after commit
  --no-commit                 Do not create a commit
  --help                      Show this help
EOF
}

log() {
  printf '\n==> %s\n' "$1"
}

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PUBLISH_SCRIPT="$ROOT_DIR/scripts/publish_release_artifacts.py"

VERSION=""
SOURCE_DIR=""
REPO_DIR="/tmp/ai-boss-release-assets"
REPO_URL="git@gitee.com:link-wei/ai-boss.git"
BRANCH_NAME="release-assets"
REMOTE_NAME="origin"
COMMIT_MESSAGE=""
DO_COMMIT="1"
DO_PUSH="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --source-dir)
      SOURCE_DIR="${2:-}"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR="${2:-}"
      shift 2
      ;;
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --branch)
      BRANCH_NAME="${2:-}"
      shift 2
      ;;
    --remote)
      REMOTE_NAME="${2:-}"
      shift 2
      ;;
    --message)
      COMMIT_MESSAGE="${2:-}"
      shift 2
      ;;
    --no-push)
      DO_PUSH="0"
      shift
      ;;
    --no-commit)
      DO_COMMIT="0"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$VERSION" || -z "$SOURCE_DIR" ]]; then
  usage >&2
  exit 1
fi

SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
REPO_DIR="$(mkdir -p "$REPO_DIR" && cd "$REPO_DIR" && pwd)"

if [[ ! -f "$PUBLISH_SCRIPT" ]]; then
  echo "Publish script not found: $PUBLISH_SCRIPT" >&2
  exit 1
fi

required_files=(
  "latest.json"
  "latest-macos.json"
  "AIBossWorkbench-Windows-Installer-v${VERSION}.exe"
  "AIBossWorkbench-macOS-v${VERSION}.zip"
)
for name in "${required_files[@]}"; do
  if [[ ! -f "$SOURCE_DIR/$name" ]]; then
    echo "Missing required artifact: $SOURCE_DIR/$name" >&2
    exit 1
  fi
done

if [[ -z "$COMMIT_MESSAGE" ]]; then
  COMMIT_MESSAGE="release: sync v${VERSION}"
fi

log "准备 Gitee 发布仓"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  rm -rf "$REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"
git remote set-url "$REMOTE_NAME" "$REPO_URL"
git fetch "$REMOTE_NAME" --prune

if [[ -n "$(git status --short)" ]]; then
  echo "Target repo has uncommitted changes: $REPO_DIR" >&2
  exit 1
fi

if git show-ref --verify --quiet "refs/remotes/$REMOTE_NAME/$BRANCH_NAME"; then
  if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
    git switch "$BRANCH_NAME"
    git reset --hard "$REMOTE_NAME/$BRANCH_NAME"
  else
    git switch -c "$BRANCH_NAME" --track "$REMOTE_NAME/$BRANCH_NAME"
  fi
else
  if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
    git switch "$BRANCH_NAME"
  else
    git switch --orphan "$BRANCH_NAME"
  fi
  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
fi

mkdir -p release
find release -mindepth 1 -maxdepth 1 -exec rm -rf {} +

log "复制 GitHub 构建产物"
cp -f "$SOURCE_DIR"/* release/

log "生成 Gitee 分发目录"
python3 "$PUBLISH_SCRIPT" \
  --platform windows \
  --public-root "$PWD" \
  --release-dir "$PWD/release" \
  --version "$VERSION"

python3 "$PUBLISH_SCRIPT" \
  --platform macos \
  --public-root "$PWD" \
  --release-dir "$PWD/release" \
  --version "$VERSION"

log "准备提交"
git add "latest.json" "latest-macos.json" "releases/$VERSION"

if [[ -z "$(git diff --cached --name-only)" ]]; then
  echo "No release changes to commit."
  exit 0
fi

if [[ "$DO_COMMIT" == "1" ]]; then
  git commit -m "$COMMIT_MESSAGE"
else
  echo "Commit skipped (--no-commit)."
fi

if [[ "$DO_PUSH" == "1" && "$DO_COMMIT" == "1" ]]; then
  log "推送到 Gitee"
  git push "$REMOTE_NAME" "$BRANCH_NAME"
else
  echo "Push skipped."
fi

log "完成"
echo "Branch   : $BRANCH_NAME"
echo "Version  : $VERSION"
echo "Repo dir : $REPO_DIR"
