@echo off
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0scripts\install-redcn.py" %*
) else (
  python "%~dp0scripts\install-redcn.py" %*
)
