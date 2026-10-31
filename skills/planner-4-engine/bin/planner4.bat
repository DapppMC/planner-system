@echo off
rem planner4.bat - Windows entry point for the Planner AI 4.0 engine. Forwards every argument to the `planner4` script next to it.
rem Needs Python 3.10+ on PATH (the `py -3` launcher is tried first, then `python`). Set PLANNER4_HOME if the engine is not
rem in the repository this file lives in.
setlocal
set "HERE=%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%HERE%planner4" %*
) else (
  where python >nul 2>nul
  if %ERRORLEVEL%==0 (
    python "%HERE%planner4" %*
  ) else (
    echo planner4: Python 3.10+ was not found on PATH. Install it from python.org and tick "Add to PATH". 1>&2
    exit /b 2
  )
)
exit /b %ERRORLEVEL%
