param(
    [string]$BaseUrl = "",
    [switch]$SkipManifest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Resolve-Root {
    if ($PSScriptRoot) {
        return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    }
    return (Get-Location).Path
}

function Get-InnoSetupCompiler {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) {
            return $path
        }
    }
    return $null
}

function Ensure-InnoSetup {
    $compiler = Get-InnoSetupCompiler
    if ($compiler) {
        return $compiler
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "未检测到 Inno Setup 6，且系统没有 winget。请先安装 Inno Setup 6。"
    }
    Write-Step "未检测到 Inno Setup，尝试通过 winget 安装"
    winget install --id JRSoftware.InnoSetup --exact --accept-package-agreements --accept-source-agreements --silent | Out-Null
    $compiler = Get-InnoSetupCompiler
    if (-not $compiler) {
        throw "Inno Setup 安装后仍未找到 ISCC.exe，请手动检查。"
    }
    return $compiler
}

$root = Resolve-Root
Set-Location $root

if (-not (Test-Path "app_version.txt")) {
    throw "未找到 app_version.txt"
}
$version = (Get-Content "app_version.txt" -Raw).Trim()
if (-not $version) {
    throw "app_version.txt 为空"
}

Write-Host "Windows 打包版本: $version" -ForegroundColor Green

Write-Step "准备虚拟环境"
if (-not (Test-Path ".venv")) {
    py -3 -m venv .venv
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "未找到虚拟环境 python: $python"
}

Write-Step "安装依赖"
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt pyinstaller

Write-Step "清理旧产物"
foreach ($path in @("build", "dist")) {
    if (Test-Path $path) {
        Remove-Item $path -Recurse -Force
    }
}
if (-not (Test-Path "release")) {
    New-Item -ItemType Directory -Path "release" | Out-Null
}

Write-Step "执行 PyInstaller"
$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--windowed",
    "--name", "AIBossWorkbench",
    "--add-data", "app_version.txt;."
)
if (Test-Path "app_config.json") {
    Write-Step "打包配置：包含 app_config.json"
    $pyinstallerArgs += @("--add-data", "app_config.json;.")
} else {
    Write-Warning "app_config.json not found. Installer will use default empty config."
}
$pyinstallerArgs += "main.py"
& $python @pyinstallerArgs

$distDir = Join-Path $root "dist\AIBossWorkbench"
if (-not (Test-Path $distDir)) {
    throw "未生成 dist\AIBossWorkbench 目录。"
}

Write-Step "生成 ZIP"
$zipPath = Join-Path $root ("release\AIBossWorkbench-Windows-v{0}.zip" -f $version)
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}
Compress-Archive -Force -Path (Join-Path $distDir "*") -DestinationPath $zipPath

Write-Step "生成安装器 EXE"
$iscc = Ensure-InnoSetup
& $iscc "windows_installer.iss"

$installerPath = Join-Path $root ("release\AIBossWorkbench-Windows-Installer-v{0}.exe" -f $version)
if (-not (Test-Path $installerPath)) {
    throw "安装器未生成: $installerPath"
}

$manifestGenerated = $false
if (-not $SkipManifest) {
    $finalBaseUrl = $BaseUrl.Trim()
    if (-not $finalBaseUrl) {
        Write-Warning "BaseUrl 未配置，跳过 latest.json 生成。请改用你自己的静态更新地址。"
    } else {
        Write-Step "生成 latest.json"
        & $python "scripts\generate_windows_update_manifest.py" `
            --base-url $finalBaseUrl `
            --installer $installerPath `
            --out "release\latest.json"
        $manifestGenerated = $true
    }
}

Write-Step "打包完成"
Write-Host "版本号           : $version"
Write-Host "运行目录         : $distDir"
Write-Host "ZIP              : $zipPath"
Write-Host "安装器           : $installerPath"
if ($manifestGenerated) {
    Write-Host "更新清单         : $(Join-Path $root 'release\latest.json')"
} elseif ($SkipManifest) {
    Write-Host "更新清单         : 已跳过 (--SkipManifest)"
} else {
    Write-Host "更新清单         : 已跳过（未配置 BaseUrl）"
}
