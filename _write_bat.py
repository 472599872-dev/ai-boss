import os
bat_content = '@echo off\r\ncd /d "%~dp0"\r\nset QTWEBENGINE_DISABLE_SANDBOX=1\r\nset QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox --disable-dev-shm-usage\r\nstart "" "AI\u62db\u8058\u5de5\u4f5c\u53f0.exe"\r\n'
bat_path = os.path.join('dist', 'AI\u62db\u8058\u5de5\u4f5c\u53f0', '\u542f\u52a8\u5de5\u4f5c\u53f0.bat')
with open(bat_path, 'w', encoding='gbk', newline='') as f:
    f.write(bat_content)
print(f"Written: {bat_path}")
