# Script para reiniciar o Waitress de forma segura
# Executa automaticamente como Administrador se necessário

$PID_TO_KILL = 13936
$SITE_PATH = "C:\Users\ti02\Desktop\site"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Reiniciando Waitress (Portal JP)" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# 1. Parar o processo atual
Write-Host "[1/3] Parando processo Waitress (PID: $PID_TO_KILL)..." -ForegroundColor Yellow
try {
    Stop-Process -Id $PID_TO_KILL -Force -ErrorAction Stop
    Write-Host "      Processo parado com sucesso!" -ForegroundColor Green
    Start-Sleep -Seconds 2
} catch {
    Write-Host "      AVISO: Processo nao encontrado ou ja foi encerrado." -ForegroundColor Yellow
}

# 2. Aguardar porta ser liberada
Write-Host "`n[2/3] Aguardando porta 5000 ser liberada..." -ForegroundColor Yellow
$attempts = 0
$maxAttempts = 10
while ($attempts -lt $maxAttempts) {
    $portInUse = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
    if (-not $portInUse) {
        Write-Host "      Porta 5000 liberada!" -ForegroundColor Green
        break
    }
    $attempts++
    Write-Host "      Aguardando... (tentativa $attempts/$maxAttempts)" -ForegroundColor Gray
    Start-Sleep -Seconds 1
}

if ($attempts -eq $maxAttempts) {
    Write-Host "      ERRO: Porta 5000 ainda em uso apos 10 segundos!" -ForegroundColor Red
    Write-Host "      Execute manualmente: netstat -ano | findstr :5000" -ForegroundColor Yellow
    exit 1
}

# 3. Reiniciar Waitress
Write-Host "`n[3/3] Iniciando Waitress com codigo atualizado..." -ForegroundColor Yellow
Set-Location $SITE_PATH

# Inicia em background
$process = Start-Process -FilePath "$SITE_PATH\venv\Scripts\python.exe" `
    -ArgumentList "run.py" `
    -WorkingDirectory $SITE_PATH `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 3

# Verifica se iniciou corretamente
if ($process.HasExited) {
    Write-Host "      ERRO: Waitress falhou ao iniciar!" -ForegroundColor Red
    Write-Host "      Execute manualmente: python run.py" -ForegroundColor Yellow
    exit 1
}

Write-Host "      Waitress iniciado com sucesso! (PID: $($process.Id))" -ForegroundColor Green

# 4. Validar que esta respondendo
Write-Host "`n[4/4] Validando que o portal esta respondendo..." -ForegroundColor Yellow
Start-Sleep -Seconds 2

try {
    $response = Invoke-WebRequest -Uri "http://localhost:5000/health" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "      Portal esta ONLINE e respondendo!" -ForegroundColor Green
} catch {
    Write-Host "      AVISO: Portal iniciou mas nao responde no /health" -ForegroundColor Yellow
    Write-Host "      Verifique manualmente: http://localhost:5000" -ForegroundColor Yellow
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Reinicio completo!" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

Write-Host "Novo PID: $($process.Id)" -ForegroundColor White
Write-Host "URL: http://localhost:5000" -ForegroundColor White
Write-Host "`nPressione qualquer tecla para sair..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
