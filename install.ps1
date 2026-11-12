#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installation all-in-one de Cert Monitor v2 sur Windows Server 2019/2022 (IIS 10).

.DESCRIPTION
    Installe Python si absent, cree l'environnement virtuel, installe les dependances,
    genere le fichier .env a partir des parametres, ouvre le port du dashboard dans le
    pare-feu, installe et demarre le service Windows (NSSM, avec repli sur tache planifiee),
    puis verifie que l'application repond.

.EXAMPLE
    .\install.ps1 -SmtpHost smtp.exemple.com -SmtpUser alertes@exemple.com -SmtpPassword "xxx" `
        -MailFrom alertes@exemple.com -MailTo "admin@exemple.com" -AcmeEmail admin@exemple.com `
        -AcmeWebroot "C:\inetpub\wwwroot" -AcmeStaging

.EXAMPLE
    .\install.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\cert-monitor",
    [int]$Port = 5000,
    [string]$ServiceName = "CertMonitor",

    [string]$SmtpHost = "",
    [int]$SmtpPort = 587,
    [string]$SmtpUser = "",
    [string]$SmtpPassword = "",
    [string]$MailFrom = "",
    [string]$MailTo = "",

    [string]$AcmeEmail = "",
    [string]$AcmeDirectoryUrl = "https://acme-v02.api.letsencrypt.org/directory",
    [ValidateSet("webroot", "standalone")]
    [string]$AcmeChallengeMode = "webroot",
    [string]$AcmeWebroot = "C:\inetpub\wwwroot",
    [int]$AcmeStandalonePort = 80,
    [string]$AcmePfxPassword = "",
    [int]$RenewBeforeDays = 30,

    [int]$WarnDays = 30,
    [int]$CriticalDays = 7,
    [string]$NotifyDays = "30",
    [int]$CheckIntervalHours = 6,

    [switch]$AcmeStaging,
    [switch]$Force,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Step { param($Message) Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param($Message) Write-Host "    OK : $Message" -ForegroundColor Green }
function Write-Warn2{ param($Message) Write-Host "    ATTENTION : $Message" -ForegroundColor Yellow }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $InstallDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$LogsDir = Join-Path $InstallDir "logs"
$DataDir = Join-Path $InstallDir "data"

if ($Uninstall) {
    Write-Step "Desinstallation de $ServiceName"
    $nssm = Get-Command nssm -ErrorAction SilentlyContinue
    $nssmLocal = Join-Path $InstallDir "tools\nssm.exe"
    $tool = if ($nssm) { $nssm.Source } elseif (Test-Path $nssmLocal) { $nssmLocal } else { $null }
    if ($tool) {
        & $tool stop $ServiceName 2>$null | Out-Null
        & $tool remove $ServiceName confirm 2>$null | Out-Null
        Write-Ok "Service $ServiceName supprime"
    } else {
        $task = Get-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
        if ($task) { Unregister-ScheduledTask -TaskName $ServiceName -Confirm:$false; Write-Ok "Tache planifiee supprimee" }
    }
    Write-Host "`nLes fichiers restent dans $InstallDir (supprimez-les manuellement si besoin)."
    return
}

Write-Step "Verification des prerequis"
$os = Get-CimInstance Win32_OperatingSystem
Write-Host "    OS : $($os.Caption) ($($os.Version))"
if ($os.Caption -notmatch "2019|2022") {
    Write-Warn2 "Windows Server 2019/2022 non detecte, installation poursuivie quand meme."
}
if (Get-Command New-WebBinding -ErrorAction SilentlyContinue) {
    Write-Ok "IIS detecte (cmdlets WebAdministration disponibles)"
} else {
    Write-Warn2 "IIS non detecte : l'installation automatique des certificats dans IIS echouera. Installez IIS (Web-Server) si besoin."
}

if (-not (Test-Path $ScriptDir\app.py)) {
    throw "Lancez ce script depuis le dossier du projet (app.py introuvable a cote de install.ps1)."
}

Write-Step "Copie des fichiers vers $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir, $LogsDir, $DataDir | Out-Null
Copy-Item "$ScriptDir\*" -Destination $InstallDir -Recurse -Force -Exclude ".venv", "data", "logs", "__pycache__", ".git"
Write-Ok "Fichiers copies"

Write-Step "Verification de Python"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$needInstall = $true
if ($pythonCmd) {
    $version = & $pythonCmd.Source --version 2>&1
    if ($version -match "Python 3\.(9|1[0-9])") {
        Write-Ok "Python trouve : $version"
        $needInstall = $false
        $global:PythonLauncher = $pythonCmd.Source
    }
}
if ($needInstall) {
    Write-Host "    Installation de Python 3.12 (silencieuse)..."
    $installer = Join-Path $env:TEMP "python-3.12.8-amd64.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile $installer -UseBasicParsing
    Start-Process -FilePath $installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_launcher=1" -Wait
    Remove-Item $installer -Force
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) { throw "Python n'a pas pu etre installe automatiquement. Installez Python 3.12 puis relancez." }
    $global:PythonLauncher = $pythonCmd.Source
    Write-Ok "Python installe : $(& $pythonCmd.Source --version 2>&1)"
}

Write-Step "Creation de l'environnement virtuel et installation des dependances"
if (-not (Test-Path $PythonExe)) {
    & $global:PythonLauncher -m venv $VenvDir
    Write-Ok "Environnement virtuel cree"
}
& $PythonExe -m pip install --upgrade pip -q
& $PythonExe -m pip install -r (Join-Path $InstallDir "requirements.txt") -q
Write-Ok "Dependances installees (flask, waitress, cryptography)"

Write-Step "Generation du fichier .env"
$envFile = Join-Path $InstallDir ".env"
if ((Test-Path $envFile) -and -not $Force) {
    Write-Warn2 ".env existe deja, conservé (utilisez -Force pour l'ecraser)."
} else {
    if (-not $AcmePfxPassword) {
        $AcmePfxPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
        Write-Host "    Mot de passe PFX genere aleatoirement."
    }
    $directoryUrl = if ($AcmeStaging) { "https://acme-staging-v02.api.letsencrypt.org/directory" } else { $AcmeDirectoryUrl }
    $lines = @(
        "DB_PATH=data/monitor.db",
        "",
        "# --- Surveillance ---",
        "WARN_DAYS=$WarnDays",
        "CRITICAL_DAYS=$CriticalDays",
        "NOTIFY_DAYS=$NotifyDays",
        "CHECK_INTERVAL_HOURS=$CheckIntervalHours",
        "",
        "# --- E-mail (SMTP) ---",
        "SMTP_HOST=$SmtpHost",
        "SMTP_PORT=$SmtpPort",
        "SMTP_USER=$SmtpUser",
        "SMTP_PASSWORD=$SmtpPassword",
        "SMTP_USE_TLS=true",
        "MAIL_FROM=$MailFrom",
        "MAIL_TO=$MailTo",
        "",
        "# --- ACME / Let's Encrypt ---",
        "ACME_DIRECTORY_URL=$directoryUrl",
        "ACME_EMAIL=$AcmeEmail",
        "ACME_ACCOUNT_KEY_PATH=data/acme_account.key",
        "ACME_OUTPUT_DIR=data/certs",
        "ACME_CHALLENGE_MODE=$AcmeChallengeMode",
        "ACME_WEBROOT=$AcmeWebroot",
        "ACME_STANDALONE_PORT=$AcmeStandalonePort",
        "ACME_PFX_PASSWORD=$AcmePfxPassword",
        "RENEW_BEFORE_DAYS=$RenewBeforeDays",
        "",
        "BASE_URL=http://localhost:$Port"
    )
    Set-Content -Path $envFile -Value $lines -Encoding UTF8
    Write-Ok ".env genere : $envFile"
}

Write-Step "Regles de pare-feu (dashboard + defi standalone)"
if (-not (Get-NetFirewallRule -DisplayName "Cert Monitor Dashboard" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Cert Monitor Dashboard" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Ok "Port $Port ouvert (dashboard)"
} else { Write-Ok "Regle dashboard deja presente" }
if ($AcmeChallengeMode -eq "standalone" -and -not (Get-NetFirewallRule -DisplayName "Cert Monitor ACME" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Cert Monitor ACME" -Direction Inbound -Protocol TCP -LocalPort $AcmeStandalonePort -Action Allow | Out-Null
    Write-Ok "Port $AcmeStandalonePort ouvert (defi ACME)"
}

Write-Step "Installation du service Windows '$ServiceName'"
$toolsDir = Join-Path $InstallDir "tools"
$nssmExe = Join-Path $toolsDir "nssm.exe"
$nssmPath = Get-Command nssm -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue
if (-not $nssmPath -and -not (Test-Path $nssmExe)) {
    try {
        New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
        $zip = Join-Path $env:TEMP "nssm.zip"
        Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $toolsDir -Force
        Copy-Item (Join-Path $toolsDir "nssm-2.24\win64\nssm.exe") $nssmExe -Force
        Remove-Item (Join-Path $toolsDir "nssm-2.24") -Recurse -Force
        Remove-Item $zip -Force
        $nssmPath = $nssmExe
        Write-Ok "NSSM telecharge"
    } catch {
        Write-Warn2 "Telechargement NSSM impossible : $($_.Exception.Message)"
    }
} elseif (Test-Path $nssmExe) {
    $nssmPath = $nssmExe
}

$serviceInstalled = $false
if ($nssmPath -and (Test-Path $nssmPath)) {
    & $nssmPath remove $ServiceName confirm 2>$null | Out-Null
    & $nssmPath install $ServiceName $PythonExe "run.py" | Out-Null
    & $nssmPath set $ServiceName AppDirectory $InstallDir | Out-Null
    & $nssmPath set $ServiceName AppStdout (Join-Path $LogsDir "service-out.log") | Out-Null
    & $nssmPath set $ServiceName AppStderr (Join-Path $LogsDir "service-err.log") | Out-Null
    & $nssmPath set $ServiceName AppRotateFiles 1 | Out-Null
    & $nssmPath set $ServiceName AppRotateBytes 5242880 | Out-Null
    & $nssmPath set $ServiceName Start SERVICE_AUTO_START | Out-Null
    $serviceInstalled = $true
    Write-Ok "Service NSSM installe (demarrage auto, logs dans $LogsDir)"
} else {
    Write-Warn2 "NSSM indisponible : installation via tache planifiee au demarrage."
    $action = New-ScheduledTaskAction -Execute $PythonExe -Argument "run.py" -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
    Register-ScheduledTask -TaskName $ServiceName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
    $serviceInstalled = $true
    Write-Ok "Tache planifiee '$ServiceName' creee (SYSTEM, au demarrage)"
}

Write-Step "Demarrage du service"
if ($nssmPath -and $serviceInstalled) {
    Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    Write-Ok "Service : $($svc.Status)"
} else {
    Start-ScheduledTask -TaskName $ServiceName
    Write-Ok "Tache planifiee demarree"
}

Write-Step "Test de sante"
$healthy = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$Port/api/config" -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
}
if ($healthy) {
    Write-Ok "Application en ligne sur http://localhost:$Port"
} else {
    Write-Warn2 "L'application ne repond pas encore. Consultez $LogsDir\service-err.log"
}

Write-Host ""
Write-Host "================= INSTALLATION TERMINEE =================" -ForegroundColor Green
Write-Host " Dashboard      : http://localhost:$Port"
Write-Host " Dossier        : $InstallDir"
Write-Host " Configuration  : $envFile"
Write-Host " Logs           : $LogsDir"
Write-Host ""
Write-Host " Pensez a :" -ForegroundColor Yellow
Write-Host "  - renseigner SMTP_HOST / MAIL_TO si ce n'est pas fait (dashboard -> .env -> redemarrer le service)"
if (-not $AcmeStaging) {
    Write-Host "  - tester d'abord avec -AcmeStaging (limites de taux Let's Encrypt production)"
}
Write-Host "==========================================================" -ForegroundColor Green
