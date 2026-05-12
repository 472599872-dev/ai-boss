# GitHub 构建，Gitee 分发

目标：

1. Gitee 继续作为主代码仓库
2. GitHub 只负责运行 GitHub Actions 打包 Windows / macOS
3. 打包产物自动同步到公开的 Gitee 分发仓
4. 客户端只从 Gitee 拉取 `latest.json` / `latest-macos.json`

## 推荐仓库结构

当前按你的仓库直接配置：

1. Gitee 主仓库：`https://gitee.com/link-wei/ai-boss.git`
2. GitHub 构建仓库：`https://github.com/MilkTeaCoder/mova-esb.git`
3. Gitee 对外分发分支：`release-assets`

也就是不再单独新建分发仓，而是在 `link-wei/ai-boss` 里用 `release-assets` 分支专门承载安装包和更新清单。

`release-assets` 分支目录结构如下：

```text
release-assets/
  latest.json
  latest-macos.json
  releases/
    1.0.18/
      AIBossWorkbench-Windows-Installer-v1.0.18.exe
      AIBossWorkbench-Windows-v1.0.18.zip
      latest.json
      AIBossWorkbench-macOS-v1.0.18.zip
      AIBossWorkbench-macOS-v1.0.18.dmg
      AIBossWorkbench-macOS-v1.0.18-x64.zip
      AIBossWorkbench-macOS-v1.0.18-x64.dmg
      latest-macos.json
```

## 工作流

工作流文件：

- [release-to-gitee.yml](/Users/weiyifeng/ai-boss/source_share_clean_20260506/.github/workflows/release-to-gitee.yml)

执行顺序：

1. 你把代码和 tag 同步到 GitHub 镜像仓
2. GitHub Actions 在 `windows-latest` 上构建 Windows 安装包
3. GitHub Actions 在 `macos-14` / `macos-13` 上构建 macOS 安装包
4. 最后一个发布 job 把所有产物合并
5. 发布 job 通过 SSH 推送到 `link-wei/ai-boss` 的 `release-assets` 分支
6. 用户和客户端都只访问 Gitee 分发地址

## GitHub Secrets

必须配置：

- `APP_CONFIG_JSON`
- `GITEE_RELEASE_SSH_KEY`

macOS 还必须配置：

- `MACOS_CERTIFICATE_P12_BASE64`
- `MACOS_CERTIFICATE_PASSWORD`
- `MACOS_CODESIGN_IDENTITY`
- `MACOS_NOTARY_APPLE_ID`
- `MACOS_NOTARY_PASSWORD`
- `MACOS_NOTARY_TEAM_ID`

当前默认值已经写进工作流：

- `GITEE_RELEASE_REPO`：默认 `link-wei/ai-boss`
- `GITEE_RELEASE_BRANCH`：默认 `release-assets`
- `GITEE_RELEASE_BASE_URL`：默认 `https://gitee.com/link-wei/ai-boss/raw/release-assets`

这 3 个 secret 现在都可以不配，只有在你以后想换仓库、分支或域名时才需要覆盖。

`GITEE_RELEASE_SSH_KEY` 对应的公钥，需要加入 `link-wei/ai-boss` 可写账户的 SSH Keys。

## 发版方式

日常开发仍然优先推到 Gitee，但用于构建的 tag 必须同时到达 GitHub 仓库 `MilkTeaCoder/mova-esb`。

每次发版：

1. 修改 `app_version.txt`
2. 提交代码
3. 推送到 Gitee
4. 推送同一提交到 GitHub 镜像仓
5. 创建并推送同版本 tag 到两个远端

```bash
git push gitee main
git push origin main
git tag v1.0.18
git push gitee v1.0.18
git push origin v1.0.18
```

如果 tag 已经存在，也可以在 GitHub Actions 页面手工执行 `workflow_dispatch`，并填入已有 tag。

## 客户端配置

客户端只需要指向 Gitee 分发地址：

```json
{
  "windows_update_enabled": true,
  "windows_update_manifest_url": "https://gitee.com/link-wei/ai-boss/raw/release-assets/latest.json",
  "macos_update_enabled": true,
  "macos_update_manifest_url": "https://gitee.com/link-wei/ai-boss/raw/release-assets/latest-macos.json"
}
```

对应的版本目录地址就是：

- `https://gitee.com/link-wei/ai-boss/raw/release-assets/releases/1.0.18/...`

## 为什么不用 Gitee 本机打包

原因很简单：

1. Gitee 更适合作为用户访问入口和国内下载源
2. GitHub Actions 原生提供 Windows / macOS 托管 runner
3. 你不需要自备一台 Windows 构建机和一台 Mac 构建机常驻在线

## 为什么不用 Gitee Release 附件

不建议把安装包直接传到 Gitee Release 附件：

1. 当前 macOS 安装包通常超过 100MB
2. Release 附件容量限制更容易成为瓶颈
3. 专用分发仓分支更容易维护 `latest.json` 和稳定下载地址

## 旧方案

仓库里保留了完全自建构建机的旧方案，相关文件如下：

- [gitee_release_receiver.py](/Users/weiyifeng/ai-boss/source_share_clean_20260506/scripts/gitee_release_receiver.py)
- [release_automation.example.env](/Users/weiyifeng/ai-boss/source_share_clean_20260506/release_automation.example.env)

如果以后你决定不再依赖 GitHub Actions，可以切回这套 WebHook + 自建构建机方案。
