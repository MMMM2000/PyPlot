@echo off
setlocal

cd /d "%~dp0.."

set "SYNC_LOG_DIR=%CD%\artifacts"
if not exist "%SYNC_LOG_DIR%" mkdir "%SYNC_LOG_DIR%" >nul 2>nul
set "SYNC_LOG=%SYNC_LOG_DIR%\sync-main-last.log"
(echo PyPlot Codex Sync started at %DATE% %TIME%) > "%SYNC_LOG%" 2>nul
>> "%SYNC_LOG%" echo Working directory: %CD%

git status --porcelain > "%TEMP%\pyplot-sync-status.txt"
if errorlevel 1 (
    echo Failed to inspect git status.
    >> "%SYNC_LOG%" echo git status failed.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

for /f %%A in ("%TEMP%\pyplot-sync-status.txt") do set "SYNC_STATUS_SIZE=%%~zA"
if not "%SYNC_STATUS_SIZE%"=="0" (
    echo The repository has local changes. Sync is refusing to pull automatically.
    echo.
    type "%TEMP%\pyplot-sync-status.txt"
    >> "%SYNC_LOG%" echo Refused because working tree is not clean.
    type "%TEMP%\pyplot-sync-status.txt" >> "%SYNC_LOG%"
    set "SYNC_EXIT_CODE=2"
    goto finish
)

echo Fetching latest changes...
git fetch --all --prune
if errorlevel 1 (
    >> "%SYNC_LOG%" echo git fetch failed.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

echo Switching to main...
git switch main
if errorlevel 1 (
    >> "%SYNC_LOG%" echo git switch main failed.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

echo Pulling latest main with fast-forward only...
git pull --ff-only
if errorlevel 1 (
    >> "%SYNC_LOG%" echo git pull --ff-only failed.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

for /f "delims=" %%C in ('git rev-parse --short HEAD') do set "SYNC_COMMIT=%%C"
echo.
echo Synced PyPlot main to %SYNC_COMMIT%.
>> "%SYNC_LOG%" echo Synced to %SYNC_COMMIT%.
set "SYNC_EXIT_CODE=0"

:finish
echo.
echo Sync exited with code %SYNC_EXIT_CODE%.
>> "%SYNC_LOG%" echo Finished at %DATE% %TIME% with code %SYNC_EXIT_CODE%.
if not defined PYPLOT_NO_PAUSE (
    echo Press any key to close this window . . .
    pause >nul
)

exit /b %SYNC_EXIT_CODE%
