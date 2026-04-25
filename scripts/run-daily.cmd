@echo off
setlocal
set PROJECT_ROOT=%~dp0..
pushd "%PROJECT_ROOT%"
python -m ai_nikki sync
set EXIT_CODE=%ERRORLEVEL%
popd
exit /b %EXIT_CODE%

