@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "APP_NAME=AIBossWorkbench"
set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "TS=%TS: =0%"
set "OUT_ROOT=%USERPROFILE%\Desktop\CrashCollect_%APP_NAME%_%TS%"
set "OUT_DIR=%OUT_ROOT%\data"
set "ZIP_FILE=%USERPROFILE%\Desktop\CrashCollect_%APP_NAME%_%TS%.zip"

echo.
echo =============================================
echo  崩溃日志一键采集脚本 - %APP_NAME%
echo =============================================
echo.
echo 输出目录: %OUT_ROOT%
echo.

mkdir "%OUT_DIR%" 2>nul
mkdir "%OUT_DIR%\app_logs" 2>nul
mkdir "%OUT_DIR%\wer" 2>nul
mkdir "%OUT_DIR%\event_logs" 2>nul

rem 1) 采集应用日志（多路径兜底）
for %%P in (
    "%~dp0logs"
    "%LOCALAPPDATA%\%APP_NAME%\logs"
    "%APPDATA%\%APP_NAME%\logs"
    "%LOCALAPPDATA%\Programs\%APP_NAME%\logs"
) do (
    if exist "%%~P" (
        echo [OK] 复制应用日志: %%~P
        xcopy "%%~P\*" "%OUT_DIR%\app_logs\" /E /I /Y >nul
    )
)

rem 2) 采集 WER 崩溃报告
if exist "%LOCALAPPDATA%\Microsoft\Windows\WER\ReportArchive" (
    echo [OK] 复制 WER ReportArchive
    xcopy "%LOCALAPPDATA%\Microsoft\Windows\WER\ReportArchive\*" "%OUT_DIR%\wer\ReportArchive\" /E /I /Y >nul
)
if exist "%LOCALAPPDATA%\Microsoft\Windows\WER\ReportQueue" (
    echo [OK] 复制 WER ReportQueue
    xcopy "%LOCALAPPDATA%\Microsoft\Windows\WER\ReportQueue\*" "%OUT_DIR%\wer\ReportQueue\" /E /I /Y >nul
)

rem 3) 导出 Windows 应用日志（完整 + 最近3天关键错误）
echo [OK] 导出 Application.evtx
wevtutil epl Application "%OUT_DIR%\event_logs\Application.evtx" >nul 2>nul

echo [OK] 导出最近3天关键错误到文本
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$start=(Get-Date).AddDays(-3);" ^
  "$providers='Application Error','Windows Error Reporting';" ^
  "Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$start} |" ^
  "Where-Object { $_.ProviderName -in $providers -or $_.LevelDisplayName -eq 'Error' } |" ^
  "Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message |" ^
  "Sort-Object TimeCreated -Descending |" ^
  "Out-File -Encoding UTF8 '%OUT_DIR%\event_logs\Application_errors_last3days.txt'"

rem 4) 记录系统信息
echo [OK] 记录系统信息
systeminfo > "%OUT_DIR%\systeminfo.txt" 2>nul
wmic os get Caption,Version,BuildNumber /value > "%OUT_DIR%\os_version.txt" 2>nul
wmic cpu get Name /value > "%OUT_DIR%\cpu.txt" 2>nul

rem 5) 生成说明文件
(
  echo 请补充一张“可靠性监视器”截图：
  echo 1. Win + R 输入 perfmon /rel
  echo 2. 打开崩溃当天红叉详情
  echo 3. 截图保存到本文件夹同级目录
  echo.
  echo 若你知道崩溃具体时间，也请在聊天里告诉研发。
) > "%OUT_ROOT%\README_请补充截图.txt"

rem 6) 打包 zip
echo [OK] 正在打包 ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path '%OUT_ROOT%\*' -DestinationPath '%ZIP_FILE%' -Force"

echo.
if exist "%ZIP_FILE%" (
  echo =============================================
  echo  采集完成！
  echo  ZIP 文件：
  echo  %ZIP_FILE%
  echo =============================================
  echo.
  start "" "%USERPROFILE%\Desktop"
) else (
  echo [WARN] 打包失败，请手动打包目录：
  echo %OUT_ROOT%
)

pause
endlocal
