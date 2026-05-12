# Windows 打包说明

## 1. 先决条件
- Windows 10/11
- Python 3.10+（建议）
- PowerShell（系统自带）
- 可选：Inno Setup 6（生成安装器）

## 0. 自动打包（推荐）

仓库已内置 GitHub Actions 工作流：

- `.github/workflows/windows-release.yml`

首次使用前，需要在仓库 Actions Secrets / Variables 中配置：

- `APP_CONFIG_JSON`：完整 `app_config.json` 文本（用于把飞书配置和更新地址一并打进安装包）
- `RELEASE_BASE_URL`：你的公开更新根地址，例如 `https://update.example.com/releases/1.0.1`

每次发版只需：

1. 修改 `app_version.txt`（如 `1.0.1`）
2. 提交并推送代码
3. 推送同版本标签：

```bash
git tag v1.0.1
git push origin v1.0.1
```

GitHub 会自动：

1. 打 Windows 安装包
2. 在已配置 `RELEASE_BASE_URL` 时生成 `latest.json`
3. 上传 CI 产物供你分发

不要把客户端固定到公开源码仓库的 Release 地址。推荐做法是：

1. 源码仓库保持 private
2. 安装包和 `latest.json` 上传到自有域名 / OSS / CDN
3. 或者单独建一个只放安装包的公开分发仓

## 2. 生成可运行目录 + ZIP
在项目根目录双击：

`build_windows.bat`

产物：
- `dist\AI招聘工作台\`（可直接运行）
- `release\AIBossWorkbench-Windows-v版本号.zip`（可分发）
- 如果本机已安装 Inno Setup 6，还会自动生成安装器

也可以用 PowerShell 一键完整打包（自动尝试安装 Inno Setup，并生成 `latest.json`）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_installer.ps1 -BaseUrl "https://your-domain/releases/1.0.0"
```

## 3. 生成安装器（可选）
如果 `build_windows.bat` 没有自动生成安装器：

1. 安装 Inno Setup 6
2. 用 Inno Setup 打开 `windows_installer.iss`
3. 点击 Build

产物：
- `release\AIBossWorkbench-Windows-Installer-v版本号.exe`

## 4. 生成在线更新清单

在线更新依赖一个远程 `latest.json`。在项目根目录执行：

```bash
python scripts/generate_windows_update_manifest.py ^
  --base-url https://your-domain/releases/1.0.0 ^
  --installer release\AIBossWorkbench-Windows-Installer-v1.0.0.exe ^
  --out release\latest.json
```

产物：

- `release\latest.json`

把安装器和 `latest.json` 一起上传到公网地址后，在客户端配置：

- `windows_update_manifest_url`

## 5. 版本号维护

- 统一版本号文件是 `app_version.txt`
- `main.py`、打包脚本和安装器都会读取它
- 每次发版前先改这里，再重新打包

## 6. 运行注意
- 首次运行可能被 Defender 提示，选择“仍要运行”
- 程序数据默认写入用户目录，不需要管理员权限
- Windows 安装版运行态配置、数据库、日志、浏览器缓存默认在 `%APPDATA%\AIBossWorkbench\`
