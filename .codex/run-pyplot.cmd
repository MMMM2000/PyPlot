@echo off
setlocal

cd /d "%~dp0.."

echo %CMDCMDLINE% | findstr /I /C:"/k" >nul 2>nul
if not errorlevel 1 (
    echo %CMDCMDLINE% | findstr /I /C:"run-pyplot.cmd" >nul 2>nul
    if not errorlevel 1 (
        set "PYPLOT_NO_PAUSE=1"
        set "PYPLOT_CLOSE_CMD=1"
    )
)

set "PYPLOT_RUN_LOG_DIR=%CD%\artifacts"
if not exist "%PYPLOT_RUN_LOG_DIR%" mkdir "%PYPLOT_RUN_LOG_DIR%" >nul 2>nul
set "PYPLOT_RUN_LOG=%PYPLOT_RUN_LOG_DIR%\run-pyplot-last.log"
(echo PyPlot Codex Run started at %DATE% %TIME%) > "%PYPLOT_RUN_LOG%" 2>nul
>> "%PYPLOT_RUN_LOG%" echo Working directory: %CD%
>> "%PYPLOT_RUN_LOG%" echo Command line: %CMDCMDLINE%

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo PyPlot virtual environment was not found:
    echo   %PYTHON_EXE%
    echo.
    echo Re-run the Codex setup action or recreate .venv with Python 3.14.
    set "PYPLOT_EXIT_CODE=1"
    >> "%PYPLOT_RUN_LOG%" echo Missing interpreter: %PYTHON_EXE%
    goto finish
)

echo Using interpreter:
echo   %PYTHON_EXE%
echo.
>> "%PYPLOT_RUN_LOG%" echo Using interpreter: %PYTHON_EXE%

"%PYTHON_EXE%" launcher.py %*
set "PYPLOT_EXIT_CODE=%ERRORLEVEL%"
>> "%PYPLOT_RUN_LOG%" echo PyPlot exited with code %PYPLOT_EXIT_CODE%.

:finish
echo.
echo PyPlot exited with code %PYPLOT_EXIT_CODE%.
>> "%PYPLOT_RUN_LOG%" echo Finished at %DATE% %TIME%
if not defined PYPLOT_NO_PAUSE (
    echo Press any key to close this window . . .
    pause >nul
)

if defined PYPLOT_CLOSE_CMD (
    exit %PYPLOT_EXIT_CODE%
)

exit /b %PYPLOT_EXIT_CODE%
