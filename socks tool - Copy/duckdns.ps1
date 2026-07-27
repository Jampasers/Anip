<#
    duckdns.ps1 - Arahkan nifstore.duckdns.org ke IP mesin ini, dan jaga tetap sinkron.

    Token TIDAK ditaruh di skrip ini. Taruh di duckdns.json (dibuat otomatis saat
    pertama kali dijalankan) atau di environment variable DUCKDNS_TOKEN.

    Yang perlu diingat: DuckDNS cuma menerjemahkan nama -> IP. Dia tidak membuka
    port dan tidak menembus NAT. Kalau mesin ini di belakang router, TCP+UDP 1080
    tetap harus di-forward ke mesin ini seperti biasa.

    Semua pengaturan ada di duckdns.json:

        domain            label tanpa ".duckdns.org"
        token             token dari dashboard DuckDNS
        ip_mode           "auto" = IP publik yang dilihat DuckDNS (default)
                          "lan"  = IP LAN mesin ini, buat klien satu jaringan
                          "1.2.3.4" = alamat tetap, dipakai apa adanya
        interval_minutes  jarak auto-update untuk -Install

    Flag di command line (-Domain/-Token/-Ip/-Lan/-IntervalMinutes) selalu menang
    atas isi config.

    Contoh:
        powershell -ExecutionPolicy Bypass -File duckdns.ps1              # update sekali
        powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Status      # cek saja
        powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Lan         # pakai IP LAN
        powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Install     # auto tiap 5 menit
        powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Uninstall
#>

param(
    [string]$Domain,
    [string]$Token,
    [string]$Ip,
    [switch]$Lan,
    [switch]$Status,
    [switch]$Install,
    [switch]$Uninstall,
    [int]$IntervalMinutes = 5,
    [switch]$Quiet,
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ConfigPath   = Join-Path $PSScriptRoot "duckdns.json"
$LogPath      = Join-Path $PSScriptRoot "duckdns.log"
$TaskName     = "DuckDNS Updater"
$TokenPlaceholder = "PASANG-TOKEN-DUCKDNS-DISINI"

# DuckDNS hanya melayani https; .NET lama default-nya masih TLS 1.0.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- log ke file + layar ----------------------------------------------------
function Write-Log([string]$msg, [string]$color = "Gray") {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try {
        if ((Test-Path $LogPath) -and (Get-Item $LogPath).Length -gt 256KB) {
            $keep = Get-Content $LogPath -Tail 300
            Set-Content -Path $LogPath -Value $keep -Encoding utf8
        }
        Add-Content -Path $LogPath -Value $line -Encoding utf8
    } catch { }
    if (-not $Quiet) { Write-Host $line -ForegroundColor $color }
}

# --- config -----------------------------------------------------------------
function Get-Config {
    if (-not (Test-Path $ConfigPath)) {
        $template = [ordered]@{
            domain           = "nifstore"
            token            = $TokenPlaceholder
            ip_mode          = "auto"
            interval_minutes = 5
        }
        $template | ConvertTo-Json | Out-File -FilePath $ConfigPath -Encoding utf8
        Write-Host "Dibuat: $ConfigPath" -ForegroundColor Yellow
        Write-Host "Isi field `"token`" dengan token dari https://www.duckdns.org (login -> token di atas halaman)." -ForegroundColor Yellow
    }
    try {
        return Get-Content $ConfigPath -Raw | ConvertFrom-Json
    } catch {
        Write-Host "duckdns.json rusak / bukan JSON valid: $_" -ForegroundColor Red
        exit 1
    }
}

$cfg = Get-Config

# domain: -Domain > config > default. Suffix .duckdns.org dibuang, API cuma mau labelnya.
if (-not $Domain) {
    if ($cfg.domain) { $Domain = $cfg.domain } else { $Domain = "nifstore" }
}
$Domain = $Domain.Trim().ToLower() -replace '\.duckdns\.org$', ''
$Fqdn   = "$Domain.duckdns.org"

# token: -Token > env DUCKDNS_TOKEN > config
if (-not $Token) { $Token = $env:DUCKDNS_TOKEN }
if (-not $Token -and $cfg.token) { $Token = $cfg.token }
if ($Token) { $Token = $Token.Trim() }

# ip_mode: "auto" (IP publik dideteksi DuckDNS) | "lan" | alamat IPv4 tetap.
# Flag di command line selalu menang atas isi config.
$IpMode = "auto"
if ($cfg.ip_mode) { $IpMode = ([string]$cfg.ip_mode).Trim().ToLower() }
if ($IpMode -notin @("auto", "lan") -and $IpMode -notmatch '^\d{1,3}(\.\d{1,3}){3}$') {
    Write-Host "ip_mode '$IpMode' tidak dikenal - dianggap 'auto'." -ForegroundColor Yellow
    $IpMode = "auto"
}
if (-not $Ip -and -not $Lan) {
    if     ($IpMode -eq "lan") { $Lan = $true }
    elseif ($IpMode -ne "auto") { $Ip = $IpMode }
}

# interval_minutes dari config dipakai kalau -IntervalMinutes tidak ditulis eksplisit
if (-not $PSBoundParameters.ContainsKey("IntervalMinutes") -and $cfg.interval_minutes) {
    $n = 0
    if ([int]::TryParse([string]$cfg.interval_minutes, [ref]$n) -and $n -ge 1) { $IntervalMinutes = $n }
}

function Assert-Token {
    if (-not $Token -or $Token -eq $TokenPlaceholder) {
        Write-Host ""
        Write-Host "Token DuckDNS belum diisi." -ForegroundColor Red
        Write-Host "  Buka https://www.duckdns.org, login, salin token di bagian atas halaman," -ForegroundColor DarkGray
        Write-Host "  lalu tempel ke field `"token`" di: $ConfigPath" -ForegroundColor DarkGray
        Write-Host "  (alternatif: set DUCKDNS_TOKEN sebagai environment variable)" -ForegroundColor DarkGray
        Write-Host ""
        exit 1
    }
}

# --- deteksi IP -------------------------------------------------------------
function Get-PublicIp {
    foreach ($url in @("https://api.ipify.org", "https://checkip.amazonaws.com")) {
        try {
            $v = (Invoke-RestMethod -Uri $url -TimeoutSec 8).ToString().Trim()
            if ($v) { return $v }
        } catch { }
    }
    return $null
}

function Get-LanIp {
    $c = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null } | Select-Object -First 1
    if ($c) { return $c.IPv4Address.IPAddress }
    return $null
}

function Resolve-Fqdn([string]$name) {
    # pakai resolver publik dulu supaya tidak kena cache DNS lokal
    foreach ($server in @("1.1.1.1", $null)) {
        try {
            if ($server) {
                $r = Resolve-DnsName -Name $name -Type A -Server $server -DnsOnly -ErrorAction Stop
            } else {
                $r = Resolve-DnsName -Name $name -Type A -ErrorAction Stop
            }
            $a = $r | Where-Object { $_.IPAddress } | Select-Object -First 1
            if ($a) { return $a.IPAddress }
        } catch { }
    }
    return $null
}

# --- panggil API DuckDNS ----------------------------------------------------
function Update-DuckDns([string]$targetIp) {
    Assert-Token
    $url = "https://www.duckdns.org/update?domains=$Domain&token=$Token&verbose=true"
    if ($targetIp) { $url += "&ip=$targetIp" } else { $url += "&ip=" }

    try {
        $resp = (Invoke-RestMethod -Uri $url -TimeoutSec 15).ToString()
    } catch {
        Write-Log "GAGAL menghubungi duckdns.org: $_" "Red"
        return $false
    }

    # verbose=true -> "OK\n<ipv4>\n<ipv6>\n<UPDATED|NOCHANGE>"
    $parts  = ($resp -split "`n") | ForEach-Object { $_.Trim() }
    $result = $parts[0]

    if ($result -ne "OK") {
        Write-Log "DuckDNS menjawab KO - domain '$Domain' atau token salah." "Red"
        Write-Log "  Cek: domain harus persis seperti di dashboard (tanpa .duckdns.org)." "DarkGray"
        return $false
    }

    $recorded = $parts[1]
    $change   = $parts[3]
    if ($change -eq "NOCHANGE") {
        Write-Log "$Fqdn -> $recorded (tidak berubah)" "DarkGray"
    } else {
        Write-Log "$Fqdn -> $recorded (DIPERBARUI)" "Green"
    }
    return $true
}

# --- pasang / lepas scheduled task ------------------------------------------
function Test-Admin {
    return ([Security.Principal.WindowsPrincipal]`
        [Security.Principal.WindowsIdentity]::GetCurrent()`
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Request-Elevation([string[]]$extraArgs) {
    if ($Elevated) {
        Write-Host "Gagal mendapatkan hak Administrator." -ForegroundColor Red
        exit 1
    }
    Write-Host "Meminta hak Administrator..." -ForegroundColor Yellow
    $argList = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Elevated") + $extraArgs
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

function Install-Task {
    if (-not (Test-Admin)) {
        Request-Elevation @("-Install", "-IntervalMinutes", $IntervalMinutes, "-Domain", $Domain)
    }
    Assert-Token

    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -Quiet" `
        -WorkingDirectory $PSScriptRoot

    # dua trigger: saat boot, dan berulang tiap N menit sejak sekarang
    $atStartup = New-ScheduledTaskTrigger -AtStartup
    $repeat    = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atStartup, $repeat) `
        -Principal $principal -Settings $settings -Description "Sinkronkan $Fqdn ke IP mesin ini" -Force | Out-Null

    Write-Host "Task '$TaskName' terpasang: jalan saat boot + tiap $IntervalMinutes menit (sebagai SYSTEM)." -ForegroundColor Green
    Write-Host "Log     : $LogPath" -ForegroundColor DarkGray
    Write-Host "Hapus   : powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Uninstall" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "Update pertama sekarang:" -ForegroundColor Cyan
    Update-DuckDns $Ip | Out-Null
}

function Uninstall-Task {
    if (-not (Test-Admin)) { Request-Elevation @("-Uninstall") }
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Task '$TaskName' dihapus." -ForegroundColor Green
    } else {
        Write-Host "Task '$TaskName' memang tidak terpasang." -ForegroundColor DarkGray
    }
}

function Show-Status {
    Write-Host "=== Status $Fqdn ===" -ForegroundColor Cyan

    if (-not $Token -or $Token -eq $TokenPlaceholder) {
        Write-Host "  Token    : BELUM DIISI ($ConfigPath)" -ForegroundColor Red
    } else {
        $hint = $Token.Substring(0, [Math]::Min(4, $Token.Length))
        Write-Host "  Token    : terisi ($hint...)" -ForegroundColor Green
    }
    Write-Host "  Mode IP  : $IpMode" -ForegroundColor Gray

    $dns = Resolve-Fqdn $Fqdn
    $pub = Get-PublicIp
    $lan = Get-LanIp

    if ($dns) { Write-Host "  DNS saat ini : $dns" -ForegroundColor Green }
    else      { Write-Host "  DNS saat ini : belum ada record A" -ForegroundColor Yellow }
    Write-Host "  IP publik    : $pub" -ForegroundColor Gray
    Write-Host "  IP LAN       : $lan" -ForegroundColor Gray

    if ($dns -and $pub -and $dns -eq $pub) {
        Write-Host "  -> cocok dengan IP publik." -ForegroundColor Green
    } elseif ($dns -and $lan -and $dns -eq $lan) {
        Write-Host "  -> menunjuk ke IP LAN (mode -Lan, hanya jalan dari jaringan lokal)." -ForegroundColor Yellow
    } elseif ($dns) {
        Write-Host "  -> TIDAK cocok. Jalankan skrip ini tanpa -Status untuk memperbarui." -ForegroundColor Yellow
    }

    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        $info = $t | Get-ScheduledTaskInfo
        Write-Host "  Auto-update  : aktif (terakhir jalan $($info.LastRunTime), hasil $($info.LastTaskResult))" -ForegroundColor Green
    } else {
        Write-Host "  Auto-update  : belum dipasang (-Install untuk memasang)" -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host "Klien konek ke: ${Fqdn}:1080:hanif:hanif" -ForegroundColor Cyan
    Write-Host "(port 1080 tetap harus terbuka / di-forward - DuckDNS tidak membuka port)" -ForegroundColor DarkGray
}

# --- alur utama -------------------------------------------------------------
if ($Uninstall) { Uninstall-Task; return }
if ($Install)   { Install-Task;   return }
if ($Status)    { Show-Status;    return }

if (-not $Ip -and $Lan) {
    $Ip = Get-LanIp
    if (-not $Ip) {
        Write-Log "Gagal mendeteksi IP LAN. Pakai -Ip <alamat> manual." "Red"
        exit 1
    }
    Write-Log "Mode LAN: memakai $Ip" "Yellow"
}

$ok = Update-DuckDns $Ip
if (-not $ok) { exit 1 }
exit 0
