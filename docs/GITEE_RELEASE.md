# GitHub 构建，手动同步到 Gitee

目标：

1. Gitee 继续作为主代码仓库
2. GitHub 只负责运行 GitHub Actions 打包 Windows / macOS
3. GitHub Actions 生成更新清单
4. 安装包和清单发布到阿里云 OSS
5. 客户端从 OSS 拉取 `latest.json` / `latest-macos.json`

重要限制：

1. Gitee `raw` 地址会对程序化下载返回 `403`
2. 当前仓库的 `link-wei.gitee.io` 不能作为默认可用前提
3. Gitee 仓库单文件上限是 `100MB`，当前安装包超过这个限制，不能直接放进仓库分支
4. 所以默认方案改为阿里云 OSS / CDN

## 当前仓库配置

1. Gitee 主仓库：`https://gitee.com/link-wei/ai-boss.git`
2. GitHub 构建仓库：`https://github.com/MilkTeaCoder/ai-boss-workbench.git`
3. OSS Bucket：`mova-itai`
4. OSS Prefix：`ai-boss`
5. OSS 默认公网地址：`https://mova-itai.oss-cn-shanghai.aliyuncs.com/ai-boss`

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

现在只有一个可选 secret：

- `APP_CONFIG_JSON`

作用：

1. 如果配置了，构建时会把它写成 `app_config.json`
2. 如果没配置，工作流也会继续构建，但默认关闭在线更新
3. 新版本不会再默认指向 `link-wei.gitee.io`

OSS 配置：

- `OSS_ENDPOINT`
- `OSS_BUCKET_NAME`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_PREFIX`
- `OSS_PUBLIC_BASE_URL`

默认会根据 `OSS_BUCKET_NAME`、`OSS_ENDPOINT` 和 `OSS_PREFIX` 推导公网下载地址：

- `https://mova-itai.oss-cn-shanghai.aliyuncs.com/ai-boss`

如果以后接入 CDN 或自定义域名，只需要把 `OSS_PUBLIC_BASE_URL` 改成你的 CDN 地址。

## 发版方式

每次发版：

1. 修改 `app_version.txt`
2. 提交代码
3. 推送到 Gitee
4. 推送到 GitHub
5. 推送同版本 tag 到两个远端
6. GitHub Actions 自动打包
7. 自动上传 artifacts 到 OSS
8. 用户客户端从 OSS 检查和下载更新

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

不再推荐把安装包同步到 Gitee 仓库分支。

原因：

1. 当前安装包大于 `100MB`
2. Gitee 仓库分支推送会被大小限制拒绝
3. 即使只放清单，安装包下载地址仍然必须指向 OSS

如果你只是想在 Gitee 仓库里保留版本信息，可以只提交文本说明或小型 JSON 清单，不要再把安装包推到仓库分支。

## GitHub Secrets

需要配置：

```text
OSS_ENDPOINT=oss-cn-shanghai.aliyuncs.com
OSS_BUCKET_NAME=mova-itai
OSS_ACCESS_KEY_ID=<RAM AccessKey ID>
OSS_ACCESS_KEY_SECRET=<RAM AccessKey Secret>
OSS_PREFIX=ai-boss
```

可选：

```text
OSS_PUBLIC_BASE_URL=https://mova-itai.oss-cn-shanghai.aliyuncs.com/ai-boss
```

如果不配置 `OSS_PUBLIC_BASE_URL`，工作流会自动推导默认 OSS 公网地址。

## 客户端配置

客户端继续指向：

```json
{
  "windows_update_enabled": true,
  "windows_update_manifest_url": "https://mova-itai.oss-cn-shanghai.aliyuncs.com/ai-boss/latest.json",
  "windows_update_check_on_startup": true,
  "macos_update_enabled": true,
  "macos_update_manifest_url": "https://mova-itai.oss-cn-shanghai.aliyuncs.com/ai-boss/latest-macos.json",
  "macos_update_check_on_startup": true
}
```

## macOS 未签名说明

当前 GitHub Actions 产出的 macOS 包不签名、不公证。

用户首次打开时可能遇到系统阻止，这时需要手动放行，例如：

1. 在“系统设置 -> 隐私与安全性”里允许打开
2. 或者在 Finder 里右键应用，选择“打开”

如果以后你补齐 Apple 签名材料，再把工作流切回签名版即可。
