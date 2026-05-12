# GitHub 构建，手动同步到 Gitee

目标：

1. Gitee 继续作为主代码仓库
2. GitHub 只负责运行 GitHub Actions 打包 Windows / macOS
3. GitHub Actions 产出安装包和更新清单
4. 你手动把这些产物同步到 Gitee `release-assets` 分支
5. 客户端只从 Gitee 拉取 `latest.json` / `latest-macos.json`

## 当前仓库配置

1. Gitee 主仓库：`https://gitee.com/link-wei/ai-boss.git`
2. GitHub 构建仓库：`https://github.com/MilkTeaCoder/ai-boss-workbench.git`
3. Gitee 对外分发分支：`release-assets`

## GitHub Actions 做什么

工作流文件：

- [release-to-gitee.yml](/Users/weiyifeng/ai-boss/source_share_clean_20260506/.github/workflows/release-to-gitee.yml)

执行结果：

1. 在 `windows-latest` 上打 Windows 安装包
2. 在 `macos-14` 和 `macos-13` 上打 macOS 安装包
3. 上传 GitHub Actions artifacts
4. 不自动推送到 Gitee

当前 macOS 构建默认不签名、不公证。用户首次打开时需要手动信任。

## GitHub Secrets

现在只有一个真正需要的可选 secret：

- `APP_CONFIG_JSON`

作用：

1. 如果配置了，构建时会把它写成 `app_config.json`
2. 如果没配置，工作流也会继续构建，只是不注入运行配置

可选覆盖：

- `GITEE_RELEASE_BASE_URL`

默认值已经写进工作流：

- `https://gitee.com/link-wei/ai-boss/raw/release-assets`

这个值用于生成 `latest.json` / `latest-macos.json` 里的下载地址。你后续如果就是手动同步到这个分支，就连这个 secret 都不用配。

## 发版方式

每次发版：

1. 修改 `app_version.txt`
2. 提交代码
3. 推送到 Gitee
4. 推送到 GitHub
5. 推送同版本 tag 到两个远端
6. GitHub Actions 自动打包
7. 从 GitHub Actions 下载 artifacts
8. 手动同步到 Gitee `release-assets`

```bash
git push gitee main
git push origin main
git tag v1.0.19
git push gitee v1.0.19
git push origin v1.0.19
```

## 从 GitHub 下载什么

Windows artifact 里会有：

1. `AIBossWorkbench-Windows-Installer-v版本号.exe`
2. `AIBossWorkbench-Windows-v版本号.zip`
3. `latest.json`

macOS artifact 里会有：

1. `AIBossWorkbench-macOS-v版本号.zip`
2. `AIBossWorkbench-macOS-v版本号.dmg`
3. `AIBossWorkbench-macOS-v版本号-x64.zip`
4. `AIBossWorkbench-macOS-v版本号-x64.dmg`
5. `latest-macos.json`

## 如何手动同步到 Gitee

推荐仍然用 `release-assets` 分支，不要用 Gitee Release 附件。

目录结构如下：

```text
release-assets/
  latest.json
  latest-macos.json
  releases/
    1.0.19/
      AIBossWorkbench-Windows-Installer-v1.0.19.exe
      AIBossWorkbench-Windows-v1.0.19.zip
      latest.json
      AIBossWorkbench-macOS-v1.0.19.zip
      AIBossWorkbench-macOS-v1.0.19.dmg
      AIBossWorkbench-macOS-v1.0.19-x64.zip
      AIBossWorkbench-macOS-v1.0.19-x64.dmg
      latest-macos.json
```

建议直接用仓库脚本：

```bash
./scripts/sync_gitee_release_assets.sh \
  --version 1.0.19 \
  --source-dir /path/to/github-artifacts/release
```

这个脚本会自动：

1. 准备或更新本地 Gitee 发布仓
2. 切到 `release-assets` 分支
3. 复制 GitHub 下载下来的产物
4. 生成 `latest.json` / `latest-macos.json` 和 `releases/1.0.19/`
5. 提交并推送到 Gitee

如果你只想先检查，不推送：

```bash
./scripts/sync_gitee_release_assets.sh \
  --version 1.0.19 \
  --source-dir /path/to/github-artifacts/release \
  --no-push
```

## 客户端配置

客户端继续指向：

```json
{
  "windows_update_enabled": true,
  "windows_update_manifest_url": "https://gitee.com/link-wei/ai-boss/raw/release-assets/latest.json",
  "macos_update_enabled": true,
  "macos_update_manifest_url": "https://gitee.com/link-wei/ai-boss/raw/release-assets/latest-macos.json"
}
```

## macOS 未签名说明

当前 GitHub Actions 产出的 macOS 包不签名、不公证。

用户首次打开时可能遇到系统阻止，这时需要手动放行，例如：

1. 在“系统设置 -> 隐私与安全性”里允许打开
2. 或者在 Finder 里右键应用，选择“打开”

如果以后你补齐 Apple 签名材料，再把工作流切回签名版即可。
