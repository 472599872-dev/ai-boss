"""PyInstaller runtime hook - runs before main.py to disable QtWebEngine sandbox."""
import os
import sys

# Disable Chromium sandbox to prevent STATUS_STACK_BUFFER_OVERRUN (0xC0000409)
# crash when running from PyInstaller bundle
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QT_WEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-dev-shm-usage --disable-gpu-sandbox"

# Set subprocess executable path for QtWebEngineProcess
if getattr(sys, "frozen", False):
    bundle_dir = os.path.dirname(sys.executable)
    if sys.platform == "darwin":
        contents_dir = os.path.dirname(bundle_dir)
        qtwe_base = os.path.join(
            contents_dir,
            "Frameworks",
            "PySide6",
            "Qt",
            "lib",
            "QtWebEngineCore.framework",
            "Versions",
            "Resources",
        )
        qtwe_proc = os.path.join(
            qtwe_base,
            "Helpers",
            "QtWebEngineProcess.app",
            "Contents",
            "MacOS",
            "QtWebEngineProcess",
        )
        resources_dir = os.path.join(qtwe_base, "Resources")
        locales_dir = os.path.join(resources_dir, "qtwebengine_locales")
        if os.path.exists(qtwe_proc):
            os.environ["QTWEBENGINEPROCESS_PATH"] = qtwe_proc
        if os.path.isdir(resources_dir):
            os.environ["QTWEBENGINE_RESOURCES_PATH"] = resources_dir
        if os.path.isdir(locales_dir):
            os.environ["QTWEBENGINE_LOCALES_PATH"] = locales_dir
    else:
        internal_dir = os.path.join(bundle_dir, "_internal")
        pyside6_dir = os.path.join(internal_dir, "PySide6")
        qtwe_proc = os.path.join(pyside6_dir, "QtWebEngineProcess.exe")
        if os.path.exists(qtwe_proc):
            os.environ["QTWEBENGINEPROCESS_PATH"] = qtwe_proc

        resources_dir = os.path.join(pyside6_dir, "resources")
        if os.path.isdir(resources_dir):
            os.environ["QTWEBENGINE_RESOURCES_PATH"] = resources_dir

        locales_dir = os.path.join(pyside6_dir, "translations", "qtwebengine_locales")
        if os.path.isdir(locales_dir):
            os.environ["QTWEBENGINE_LOCALES_PATH"] = locales_dir
