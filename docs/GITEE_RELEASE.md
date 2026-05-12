# Gitee 发版与更新链路

目标：

1. Gitee 作为唯一源码仓库
2. 由 Gitee 的 `Tag Push` WebHook 触发 Windows / macOS 构建机
3. 构建机自动打包并把安装包、`latest.json`、`latest-macos.json` 发布到你自己的更新目录
4. 客户端只读取你自己的更新地址，不再依赖 GitHub

## 目录结构

建议让对外可访问的静态目录长这样：

```text
update-root/
  latest.json
  latest-macos.json
  releases/
    1.0.16/
      AIBossWorkbench-Windows-Installer-v1.0.16.exe
      AIBossWorkbench-Windows-v1.0.16.zip
      latest.json
      AIBossWorkbench-macOS-v1.0.16.zip
      AIBossWorkbench-macOS-v1.0.16.dmg
      latest-macos.json
```

对应公网地址示例：

```text
https://update.example.com/latest.json
https://update.example.com/latest-macos.json
https://update.example.com/releases/1.0.16/AIBossWorkbench-Windows-Installer-v1.0.16.exe
```

## 一次性准备

### 1. 在 Windows 构建机克隆仓库

```powershell
git clone git@gitee.com:link-wei/ai-boss.git C:\ci\ai-boss
cd C:\ci\ai-boss
copy release_automation.example.env release_automation.env
```

把 `release_automation.env` 改成 Windows 版本，例如：

```text
GITEE_RELEASE_SECRET=your-secret
RELEASE_PLATFORM=windows
RELEASE_LISTEN_HOST=0.0.0.0
RELEASE_LISTEN_PORT=8787
RELEASE_REPO_DIR=C:\ci\ai-boss
RELEASE_GIT_REMOTE=origin
RELEASE_BASE_URL=https://update.example.com
RELEASE_PUBLIC_ROOT=D:\update-root
RELEASE_ASSETS_SUBDIR=releases
APP_CONFIG_SOURCE_PATH=C:\secure\app_config.json
```

启动接收器：

```powershell
python .\scripts\gitee_release_receiver.py --env-file .\release_automation.env
```

### 2. 在 macOS 构建机克隆仓库

```bash
git clone git@gitee.com:link-wei/ai-boss.git /opt/ai-boss
cd /opt/ai-boss
cp release_automation.example.env release_automation.env
```

把 `release_automation.env` 改成 macOS 版本，例如：

```text
GITEE_RELEASE_SECRET=your-secret
RELEASE_PLATFORM=macos
RELEASE_LISTEN_HOST=0.0.0.0
RELEASE_LISTEN_PORT=8788
RELEASE_REPO_DIR=/opt/ai-boss
RELEASE_GIT_REMOTE=origin
RELEASE_BASE_URL=https://update.example.com
RELEASE_PUBLIC_ROOT=/srv/update-root
RELEASE_ASSETS_SUBDIR=releases
APP_CONFIG_SOURCE_PATH=/opt/secure/app_config.json
```

启动接收器：

```bash
python3 ./scripts/gitee_release_receiver.py --env-file ./release_automation.env
```

### 3. 在 Gitee 仓库里配置两个 WebHook

仓库 `管理 -> WebHooks` 中新增两个 `Tag Push` 钩子：

1. Windows：`http://你的-windows-构建机:8787/`
2. macOS：`http://你的-macos-构建机:8788/`

两个钩子都填同一个密码，并与 `GITEE_RELEASE_SECRET` 保持一致。

## 发版方式

每次发版只做这几步：

1. 修改 `app_version.txt`
2. 提交到 Gitee `main`
3. 打标签并推送标签

```bash
git push origin main
git tag v1.0.16
git push origin v1.0.16
```

触发后，构建机会自动：

1. `git fetch --tags`
2. `git checkout v1.0.16`
3. 注入 `app_config.json`
4. 打包
5. 生成 manifest
6. 把产物发布到 `RELEASE_PUBLIC_ROOT`
7. 更新根目录 `latest.json` / `latest-macos.json`

## 客户端配置

客户端只配置你自己的更新地址：

```json
{
  "windows_update_enabled": true,
  "windows_update_manifest_url": "https://update.example.com/latest.json",
  "macos_update_enabled": true,
  "macos_update_manifest_url": "https://update.example.com/latest-macos.json"
}
```

## 手动自测

你可以不用等 Gitee，也直接手工 POST：

```bash
curl -X POST http://127.0.0.1:8787/ \
  -H 'Content-Type: application/json' \
  -H 'X-Gitee-Token: your-secret' \
  -d '{"tag":"v1.0.16"}'
```

macOS 构建机同理，把端口换成 `8788`。

## 注意事项

- Windows 构建依赖 Inno Setup 6。
- macOS 构建目前会产出 ZIP 和 DMG，但如果你要更顺滑的安装体验，仍建议补齐签名和公证。
- `RELEASE_PUBLIC_ROOT` 必须是最终会被公网静态服务暴露出来的目录，或者是被反向同步到该目录的挂载点。
- 这套链路不依赖 GitHub Release，也不要求客户端访问 Gitee 附件地址。
