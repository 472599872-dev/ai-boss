# ============================================================
# AI招聘工作台 - Windows 一键打包脚本
# 用法: 在项目根目录执行 powershell -ExecutionPolicy Bypass -File build_exe.ps1
# 产出: dist\AI招聘工作台\ 文件夹（可直接拷贝到其他电脑运行）
# ============================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = (Get-Content "app_version.txt" -Raw).Trim()
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AI招聘工作台 打包工具 v$version" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---------- Step 1: 确保虚拟环境 ----------
Write-Host "[1/5] 检查虚拟环境..." -ForegroundColor Yellow
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "  创建虚拟环境..." -ForegroundColor Gray
    py -3 -m venv .venv
}
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# ---------- Step 2: 安装依赖 ----------
Write-Host "[2/5] 安装依赖..." -ForegroundColor Yellow
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r requirements.txt pyinstaller --quiet
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

# ---------- Step 3: 清理旧产物 ----------
Write-Host "[3/5] 清理旧构建..." -ForegroundColor Yellow
foreach ($dir in @("build", "dist")) {
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
}

# ---------- Step 4: PyInstaller 打包 ----------
Write-Host "[4/5] PyInstaller 打包中（请耐心等待）..." -ForegroundColor Yellow
& $python -m PyInstaller --noconfirm "build_app.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

# ---------- Step 5: 后处理 ----------
Write-Host "[5/5] 后处理..." -ForegroundColor Yellow

$distDir = Join-Path $PSScriptRoot "dist\AIBossWorkbench"
if (-not (Test-Path $distDir)) { throw "打包产物未生成" }

# 创建启动器批处理（处理 QtWebEngine 沙箱问题）
$batLines = @(
    '@echo off',
    'cd /d "%~dp0"',
    'set QTWEBENGINE_DISABLE_SANDBOX=1',
    'set QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox --disable-dev-shm-usage',
    'start "" "AIBossWorkbench.exe"'
)
$batLines -join "`r`n" | Set-Content -Path (Join-Path $distDir "启动工作台.bat") -Encoding UTF8

# 生成 ZIP 便于分发
Write-Host "  生成便携 ZIP..." -ForegroundColor Gray
if (-not (Test-Path "release")) { New-Item -ItemType Directory -Path "release" | Out-Null }
$zipName = "AI招聘工作台-v$version-portable.zip"
$zipPath = Join-Path $PSScriptRoot "release\$zipName"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $distDir "*") -DestinationPath $zipPath -Force

# ---------- 完成 ----------
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  打包完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  版本号   : $version"
Write-Host "  运行目录 : $distDir"
Write-Host "  便携ZIP  : $zipPath"
Write-Host ""
Write-Host "  使用方式:" -ForegroundColor Cyan
Write-Host "    1. 将 dist\AIBossWorkbench 整个文件夹拷贝到目标电脑"
Write-Host "    2. 双击 启动工作台.bat 即可运行"
Write-Host "    3. 或直接双击 AIBossWorkbench.exe"
Write-Host ""
Write-Host "  分发方式:" -ForegroundColor Cyan
Write-Host "    将 ZIP 文件发送给其他人，解压后即可使用"
Write-Host ""
