# Запуск турнира в Telegram: сервер + публичный HTTPS-туннель (Cloudflare) + бот.
# Использование:   .\start_telegram.ps1
# Ключ -NoBot: поднять только сервер и туннель, показать адрес и выйти (для проверки; процессы остаются работать).
param([switch]$NoBot)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 по умолчанию не включает TLS 1.2, а без него HTTPS-адрес туннеля не открыть
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Set-Location -LiteralPath $PSScriptRoot
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$port = 8000

function Get-EnvValue([string]$name) {
    if (-not (Test-Path .env)) { return '' }
    $line = Get-Content .env -Encoding UTF8 | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -Last 1
    if (-not $line) { return '' }
    return (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'")
}

# --- проверки ---
if (-not $NoBot -and -not (Get-EnvValue 'BOT_TOKEN')) {
    Write-Host "В файле .env не задан BOT_TOKEN. Получите НОВЫЙ токен у @BotFather (старый нужно отозвать командой /revoke) и впишите: BOT_TOKEN=..." -ForegroundColor Red
    exit 1
}
if (-not (Get-EnvValue 'ADMIN_TELEGRAM_IDS')) {
    Write-Host "Внимание: ADMIN_TELEGRAM_IDS пуст в .env. Приложение откроется, но админ-панели в Telegram не будет ни у кого. Впишите свой Telegram ID (узнать: @userinfobot)." -ForegroundColor Yellow
}
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "Не найден cloudflared. Установите: winget install Cloudflare.cloudflared  (и откройте новое окно PowerShell)" -ForegroundColor Red
    exit 1
}

# --- сервер ---
$server = $null
if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
    Write-Host "Запускаю сервер на порту $port..."
    $server = Start-Process -FilePath py -ArgumentList '-m', 'uvicorn', 'main:app', '--port', "$port" -PassThru -WindowStyle Minimized
    for ($i = 0; $i -lt 40; $i++) {
        try { Invoke-WebRequest "http://127.0.0.1:$port/api/leagues" -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
    }
} else {
    Write-Host "Сервер на порту $port уже запущен — использую его."
}

# --- туннель ---
$log = Join-Path $PSScriptRoot 'tunnel.log'
Remove-Item $log -ErrorAction SilentlyContinue
Write-Host "Поднимаю HTTPS-туннель..."
$tunnel = Start-Process -FilePath cloudflared -ArgumentList 'tunnel', '--url', "http://127.0.0.1:$port", '--no-autoupdate' `
    -PassThru -WindowStyle Hidden -RedirectStandardError $log

function Stop-All {
    if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue }
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
}

$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Milliseconds 700
    try {
        $fs = [IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
        $text = (New-Object IO.StreamReader($fs)).ReadToEnd(); $fs.Close()
        if ($text -match 'https://[a-z0-9-]+\.trycloudflare\.com') { $url = $Matches[0] }
    } catch { }
}
if (-not $url) { Write-Host "Не удалось получить адрес туннеля. См. tunnel.log" -ForegroundColor Red; Stop-All; exit 1 }

# Безопасность (локальная проверка, не зависит от DNS): запрос с заголовками, которые добавляет туннель,
# не должен получать права админа без подписи Telegram
$proxyHeaders = @{ 'X-Forwarded-For' = '203.0.113.7'; 'Cf-Connecting-Ip' = '203.0.113.7' }
$sim = Invoke-RestMethod "http://127.0.0.1:$port/api/auth/check" -Headers $proxyHeaders -TimeoutSec 10
if ($sim.is_admin) {
    Write-Host "ОСТАНОВЛЕНО: запросы из интернета получают права админа без Telegram. Запуск прерван." -ForegroundColor Red
    Stop-All; exit 1
}

# Доступность адреса: новый адрес появляется в DNS не мгновенно. Если этот компьютер так и не смог его открыть
# (бывает из-за DNS провайдера) — это не ошибка: Telegram на телефоне и серверы Telegram открывают его сами.
$ready = $false
$lastError = ''
for ($i = 0; $i -lt 8 -and -not $ready; $i++) {
    try {
        $check = Invoke-RestMethod "$url/api/auth/check" -TimeoutSec 5
        $ready = $true
    } catch { $lastError = $_.Exception.Message; Start-Sleep -Seconds 2 }
}
if ($ready -and $check.is_admin) {
    Write-Host "ОСТАНОВЛЕНО: через туннель выдаются права админа без Telegram. Запуск прерван." -ForegroundColor Red
    Stop-All; exit 1
}
if (-not $ready) {
    Write-Host "С этого компьютера адрес пока не открывается ($lastError)." -ForegroundColor Yellow
    Write-Host "Это нормально: на телефоне он откроется. Если Telegram на ПК не открывает приложение — смените DNS на 1.1.1.1 или пользуйтесь телефоном." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Приложение доступно по адресу: $url" -ForegroundColor Green
Write-Host "Админ-панель из интернета закрыта (проверено); откроется только вам в Telegram, по вашему ID."

if ($NoBot) {
    Write-Host "(-NoBot: сервер PID $($server.Id), туннель PID $($tunnel.Id) оставлены работать)"
    exit 0
}

# --- бот ---
$env:WEBAPP_URL = $url
try {
    py bot.py
} finally {
    Write-Host "Останавливаю туннель и сервер..."
    Stop-All
}
