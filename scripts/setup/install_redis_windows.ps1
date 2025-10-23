# Script de Instalação do Redis para Windows
# Instala Memurai (implementação de Redis para Windows)

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Instalador de Redis para Windows (Memurai)" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Verificar se está executando como Administrador
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "⚠️  AVISO: Este script precisa ser executado como Administrador" -ForegroundColor Yellow
    Write-Host "Clique com o botão direito no PowerShell e selecione 'Executar como Administrador'" -ForegroundColor Yellow
    Write-Host ""
    pause
    exit 1
}

# Opções de instalação
Write-Host "Escolha o método de instalação:" -ForegroundColor Green
Write-Host ""
Write-Host "1. Chocolatey (Recomendado - mais rápido)" -ForegroundColor White
Write-Host "2. Download Manual do Memurai" -ForegroundColor White
Write-Host "3. Docker (se você tem Docker Desktop instalado)" -ForegroundColor White
Write-Host "4. WSL2 + Redis (se você tem WSL2 configurado)" -ForegroundColor White
Write-Host ""
$choice = Read-Host "Digite o número da opção (1-4)"

switch ($choice) {
    "1" {
        Write-Host ""
        Write-Host "📦 Instalando via Chocolatey..." -ForegroundColor Cyan

        # Verificar se Chocolatey está instalado
        if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
            Write-Host "Chocolatey não encontrado. Instalando Chocolatey primeiro..." -ForegroundColor Yellow
            Set-ExecutionPolicy Bypass -Scope Process -Force
            [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
            Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

            # Recarregar PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        }

        Write-Host "Instalando Memurai Developer Edition..." -ForegroundColor Cyan
        choco install memurai-developer -y

        Write-Host ""
        Write-Host "✅ Memurai instalado com sucesso!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Iniciando serviço Memurai..." -ForegroundColor Cyan
        Start-Service Memurai

        Write-Host "✅ Memurai está rodando!" -ForegroundColor Green
    }

    "2" {
        Write-Host ""
        Write-Host "📥 Download Manual do Memurai..." -ForegroundColor Cyan
        Write-Host ""
        Write-Host "1. Acesse: https://www.memurai.com/get-memurai" -ForegroundColor Yellow
        Write-Host "2. Baixe o instalador do Memurai Developer Edition (gratuito)" -ForegroundColor Yellow
        Write-Host "3. Execute o instalador e siga as instruções" -ForegroundColor Yellow
        Write-Host "4. Após a instalação, o serviço Memurai será iniciado automaticamente" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Pressione qualquer tecla após concluir a instalação..." -ForegroundColor Cyan
        pause
    }

    "3" {
        Write-Host ""
        Write-Host "🐳 Instalando via Docker..." -ForegroundColor Cyan

        # Verificar se Docker está instalado
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            Write-Host "❌ Docker não encontrado!" -ForegroundColor Red
            Write-Host "Instale o Docker Desktop primeiro: https://www.docker.com/products/docker-desktop" -ForegroundColor Yellow
            pause
            exit 1
        }

        Write-Host "Criando container Redis..." -ForegroundColor Cyan
        docker run --name redis-cache -p 6379:6379 -d redis:7-alpine

        Write-Host ""
        Write-Host "✅ Redis está rodando no Docker!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Comandos úteis:" -ForegroundColor Yellow
        Write-Host "  docker start redis-cache    # Iniciar Redis" -ForegroundColor White
        Write-Host "  docker stop redis-cache     # Parar Redis" -ForegroundColor White
        Write-Host "  docker logs redis-cache     # Ver logs" -ForegroundColor White
    }

    "4" {
        Write-Host ""
        Write-Host "🐧 Instalando via WSL2..." -ForegroundColor Cyan

        # Verificar se WSL está disponível
        if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
            Write-Host "❌ WSL2 não encontrado!" -ForegroundColor Red
            Write-Host "Instale o WSL2 primeiro: wsl --install" -ForegroundColor Yellow
            pause
            exit 1
        }

        Write-Host "Instalando Redis no WSL2..." -ForegroundColor Cyan
        wsl -d Ubuntu sudo apt update
        wsl -d Ubuntu sudo apt install redis-server -y
        wsl -d Ubuntu sudo service redis-server start

        Write-Host ""
        Write-Host "✅ Redis instalado no WSL2!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Comandos úteis:" -ForegroundColor Yellow
        Write-Host "  wsl sudo service redis-server start    # Iniciar Redis" -ForegroundColor White
        Write-Host "  wsl sudo service redis-server stop     # Parar Redis" -ForegroundColor White
        Write-Host "  wsl redis-cli ping                     # Testar Redis" -ForegroundColor White
    }

    default {
        Write-Host "❌ Opção inválida!" -ForegroundColor Red
        pause
        exit 1
    }
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Testando Conexão com Redis" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Start-Sleep -Seconds 2

# Testar conexão
try {
    Write-Host "Testando conexão com Redis em localhost:6379..." -ForegroundColor Cyan

    $socket = New-Object System.Net.Sockets.TcpClient
    $socket.Connect("localhost", 6379)
    $stream = $socket.GetStream()
    $writer = New-Object System.IO.StreamWriter($stream)
    $reader = New-Object System.IO.StreamReader($stream)

    # Enviar comando PING
    $writer.WriteLine("PING")
    $writer.Flush()

    # Ler resposta
    $response = $reader.ReadLine()

    $stream.Close()
    $socket.Close()

    Write-Host "✅ Redis está respondendo corretamente!" -ForegroundColor Green
}
catch {
    Write-Host "⚠️  Não foi possível conectar ao Redis" -ForegroundColor Yellow
    Write-Host "Verifique se o serviço está rodando" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Configurando Variáveis de Ambiente" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Verificar se .env existe
$envFile = Join-Path $PSScriptRoot ".env"

if (Test-Path $envFile) {
    Write-Host "Arquivo .env encontrado: $envFile" -ForegroundColor Green

    $content = Get-Content $envFile -Raw

    # Verificar se REDIS_URL já existe
    if ($content -match "REDIS_URL=") {
        Write-Host "✅ REDIS_URL já está configurado no .env" -ForegroundColor Green
    } else {
        Write-Host "Adicionando configurações Redis ao .env..." -ForegroundColor Cyan

        $redisConfig = @"

# Redis Configuration (added by install_redis_windows.ps1)
REDIS_URL=redis://localhost:6379/0
REDIS_ENABLED=1
CACHE_DEFAULT_TIMEOUT=300
CACHE_KEY_PREFIX=jp_portal
"@

        Add-Content -Path $envFile -Value $redisConfig
        Write-Host "✅ Configurações Redis adicionadas ao .env" -ForegroundColor Green
    }
} else {
    Write-Host "⚠️  Arquivo .env não encontrado" -ForegroundColor Yellow
    Write-Host "Criando arquivo .env com configurações Redis..." -ForegroundColor Cyan

    $redisConfig = @"
# Redis Configuration
REDIS_URL=redis://localhost:6379/0
REDIS_ENABLED=1
CACHE_DEFAULT_TIMEOUT=300
CACHE_KEY_PREFIX=jp_portal
"@

    Set-Content -Path $envFile -Value $redisConfig
    Write-Host "✅ Arquivo .env criado com configurações Redis" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  ✅ Instalação Concluída!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Próximos passos:" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Verificar se o Redis está rodando:" -ForegroundColor White
Write-Host "   redis-cli ping" -ForegroundColor Gray
Write-Host "   (Deve retornar 'PONG')" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Iniciar a aplicação Flask:" -ForegroundColor White
Write-Host "   python run.py" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Verificar logs da aplicação:" -ForegroundColor White
Write-Host "   Procure por: '✅ Redis cache initialized successfully'" -ForegroundColor Gray
Write-Host ""
Write-Host "📖 Para mais informações, consulte: REDIS_SETUP.md" -ForegroundColor Cyan
Write-Host ""
pause
