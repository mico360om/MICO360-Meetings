# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MICO360 Meetings.

Build:  pyinstaller build/mico360.spec --noconfirm
Output: build/dist/MICO360Meetings/MICO360Meetings.exe  (one-folder)
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules
import os

# SPECPATH is the directory containing this .spec (…/build); project root is its parent.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

datas = [
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "samples", "sample_transcript.txt"), "samples"),
]
binaries = []
hiddenimports = []

# Bundle the heavyweight ML / media stacks completely.
for pkg in ("faster_whisper", "ctranslate2", "av", "tokenizers",
            "onnxruntime", "huggingface_hub", "ollama", "soundfile", "sounddevice",
            "mss", "cv2", "numpy", "soundcard"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("reportlab") + ["docx", "openpyxl", "PIL"]

block_cipher = None

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # unused transitive deps (verified not imported) — big download-size win
        "tkinter", "matplotlib", "pytest", "scipy", "pandas", "IPython", "notebook",
        "sympy", "numpy.distutils", "setuptools._distutils",
        # PySide6 Qt modules we don't use (we only use QtCore/QtGui/QtWidgets)
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQuickWidgets",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtUiTools",
        "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtPositioning", "PySide6.QtBluetooth",
        "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtWebSockets", "PySide6.QtNfc",
        "PySide6.QtWebChannel", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtHelp",
        "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer",
        "PySide6.QtQuickControls2", "PySide6.QtStateMachine", "PySide6.QtGraphs",
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MICO360Meetings",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                      # GUI app — no console window
    icon=os.path.join(ROOT, "assets", "app.ico"),
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="MICO360Meetings",
)
