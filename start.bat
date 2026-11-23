@echo off
chcp 65001 >nul
title RH Canvas Studio
cd /d "%~dp0"
echo ============================================
echo   RH Canvas Studio (Standalone Vue 3 + Flask)
echo ============================================

python -c "import flask, requests, PIL" >nul 2>&1
if errorlevel 1 (
  echo [1/3] Installing Python dependencies...
  pip install -r requirements.txt
)

if not exist "dist\index.html" (
  if not exist "node_modules" (
    echo [2/3] Installing frontend dependencies...
    call pnpm install
  )
  echo [2/3] Building Vue 3 frontend...
  call pnpm build
)

echo [3/3] Starting standalone server at http://127.0.0.1:5188 ...
start /b cmd /c "timeout /t 2 /nobreak >nul && start "" http://127.0.0.1:5188"
python -m server.app
pause
