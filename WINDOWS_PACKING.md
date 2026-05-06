# Windows 打包说明

## 1. 先决条件
- Windows 10/11
- Python 3.10+（建议）
- PowerShell（系统自带）
- 可选：Inno Setup 6（生成安装器）

## 2. 生成可运行目录 + ZIP
在项目根目录双击：

`build_windows.bat`

产物：
- `dist\AI招聘工作台\`（可直接运行）
- `release\AI招聘工作台-Windows-v版本号.zip`（可分发）
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
- `release\AI招聘工作台-Windows-Installer-v版本号.exe`

## 4. 生成在线更新清单

在线更新依赖一个远程 `latest.json`。在项目根目录执行：

```bash
python scripts/generate_windows_update_manifest.py ^
  --base-url https://your-domain/releases/1.0.0 ^
  --installer release\AI招聘工作台-Windows-Installer-v1.0.0.exe ^
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
