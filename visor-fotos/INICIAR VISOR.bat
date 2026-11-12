@echo off
chcp 65001 >nul
title Visor de fotos para iPad
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo  Falta instalar Python.
  echo  1. Descargalo de https://www.python.org/downloads/
  echo  2. Al instalar, marca la casilla "Add python.exe to PATH".
  echo  3. Vuelve a abrir este archivo.
  echo.
  pause
  exit /b 1
)

%PY% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Instalando componente de imagen ^(solo la primera vez^)...
  %PY% -m pip install --user --quiet pillow
)

%PY% visor.py %*
pause
