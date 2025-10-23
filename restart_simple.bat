@echo off
echo ========================================
echo   Reiniciando Waitress (Portal JP)
echo ========================================
echo.

echo [1/3] Parando processo Waitress...
taskkill /PID 13936 /F
if %ERRORLEVEL% NEQ 0 (
    echo ERRO: Nao foi possivel parar o processo. Execute como Administrador!
    pause
    exit /b 1
)

echo       Processo parado!
timeout /t 3 /nobreak >nul

echo.
echo [2/3] Aguardando porta 5000 ser liberada...
timeout /t 2 /nobreak >nul
echo       Porta liberada!

echo.
echo [3/3] Iniciando Waitress com codigo atualizado...
cd /d C:\Users\ti02\Desktop\site
start /min "" "venv\Scripts\python.exe" run.py
timeout /t 3 /nobreak >nul

echo       Waitress reiniciado!

echo.
echo ========================================
echo   Reinicio completo!
echo ========================================
echo.
echo URL: http://localhost:5000
echo.
echo Pressione qualquer tecla para sair...
pause >nul
