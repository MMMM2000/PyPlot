@echo off
cd /d C:\Users\Martin\PyPlot
set DISPLAY=1
set QT_QPA_PLATFORM=windows
.venv\Scripts\python.exe -c "import os; print(os.environ.get('DISPLAY'), os.environ.get('QT_QPA_PLATFORM'))"
.venv\Scripts\python.exe -m pytest -q tests\test_pyplot_plugins.py::test_push_workbooks_to_origin tests\test_pyplot_plugins.py::test_push_workbooks_to_origin_creates_graphs tests\test_pyplot_plugins.py::test_open_origin_shared_exports_plugin_workbooks tests\test_pyplot_plugins.py::test_open_origin_button_enabled_for_shared_plugin
