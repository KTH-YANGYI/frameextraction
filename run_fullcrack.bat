@echo off
setlocal

set SCRIPT_DIR=%~dp0
python "%SCRIPT_DIR%crack_analyze.py" --config "%SCRIPT_DIR%config.rawdata.fullcrack.yaml"

endlocal
