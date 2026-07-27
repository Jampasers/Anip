<#
    run_relay.ps1 - Satu perintah: buka port firewall lalu jalankan relay SOCKS5.

    Skrip minta hak Administrator sendiri untuk memasang rule firewall (TCP + UDP
    di port yang sama), lalu menjalankan socks5_relay.py di jendela yang sama.

    Yang menentukan berhasil/tidaknya UDP adalah --udp-ip: alamat itu dikirim ke
    klien sebagai tujuan paket UDP-nya, jadi harus alamat yang BISA DIJANGKAU KLIEN.
    Skrip memilihnya otomatis:

        IP publik menempel di interface (RDP/VPS biasa) -> pakai itu
        interface privat (PC rumah / VPS ber-NAT)       -> pakai IP publik
        klien ada di jaringan lokal yang sama           -> -UdpIp <IP LAN>

    Upstream default (217.181.66.23:1339) menerima nama domain, jadi --local-dns tidak
    dipakai lagi. Kalau nanti ganti pool yang menolak domain (rep=8), relay mendeteksi
    sendiri dan pindah ke resolve lokal - atau paksakan dengan -LocalDns.

    UDP didengarkan di nomor port yang sama dengan TCP (1080), karena banyak klien
    game mengabaikan port yang diiklankan dan tetap mengirim ke port proxy-nya.

    DuckDNS sudah termasuk dan JALAN OTOMATIS (butuh token di duckdns.json):
    tiap start, domain diarahkan ke alamat yang sama dengan --udp-ip, lalu semua
    tampilan memakai nama domain - baik baris di sini maupun log relay. Kalau token
    belum diisi atau update gagal, relay tetap jalan dan kembali menampilkan IP.
    Matikan bagian ini dengan -NoDuckDns.

    Domain ikut mesin yang sedang menjalankan relay, jadi jangan pasang auto-update
    (duckdns.ps1 -Install) di lebih dari satu mesin - nanti saling rebut. Kalau IP
    publik berganti saat relay hidup, restart relay: --udp-ip yang lama juga sudah
    salah, jadi memang harus start ulang, dan DNS ikut diperbarui saat itu.

    MULTI-USER (-MultiUser): satu port melayani semua buyer toko Discord. Username
    dan password klien dicocokkan ke database bot (socksus_account), dan tiap akun
    keluar lewat IP pool-nya sendiri (socksus_pool) - itulah yang dijual lewat panel
    !socksus. Pembelian dan Change IP dari bot langsung berlaku, tanpa restart relay.
    Tanpa switch ini perilakunya tetap seperti dulu: satu user/pass, satu upstream.

    Contoh:
        powershell -ExecutionPolicy Bypass -File run_relay.ps1
        powershell -ExecutionPolicy Bypass -File run_relay.ps1 -MultiUser
        powershell -ExecutionPolicy Bypass -File run_relay.ps1 -UdpIp 192.168.100.115
        powershell -ExecutionPolicy Bypass -File run_relay.ps1 -NoDuckDns
        powershell -ExecutionPolicy Bypass -File run_relay.ps1 -NoFirewall
#>

param(
    [string]$UdpIp,
    [switch]$Public,
    [int]$Port = 1080,
    [switch]$LocalDns,
    [switch]$NoFirewall,
    [switch]$NoDuckDns,
    [switch]$MultiUser,
    [string]$Db,
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$isAdmin = ([Security.Principal.WindowsPrincipal]`
    [Security.Principal.WindowsIdentity]::GetCurrent()`
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# --- kalau belum admin, ulangi diri sendiri dengan hak Administrator ---------
if (-not $isAdmin -and -not $NoFirewall -and -not $Elevated) {
    Write-Host "Meminta hak Administrator untuk membuka port..." -ForegroundColor Yellow
    $argList = @(
        "-NoExit", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"", "-Elevated", "-Port", $Port
    )
    if ($UdpIp)    { $argList += @("-UdpIp", $UdpIp) }
    if ($Public)   { $argList += "-Public" }
    if ($LocalDns)  { $argList += "-LocalDns" }
    if ($NoDuckDns) { $argList += "-NoDuckDns" }
    if ($MultiUser) { $argList += "-MultiUser" }
    if ($Db)        { $argList += @("-Db", "`"$Db`"") }
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $argList
        exit
    } catch {
        Write-Host "Gagal elevate. Lanjut tanpa mengatur firewall." -ForegroundColor Yellow
    }
}

# --- pasang rule firewall (idempotent) --------------------------------------
function Set-RelayRule([string]$name, [string]$proto, [int]$port) {
    $rule = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($rule) {
        $pf = $rule | Get-NetFirewallPortFilter
        if ($pf.Protocol -eq $proto -and "$($pf.LocalPort)" -eq "$port") {
            Write-Host "  rule '$name' sudah benar." -ForegroundColor DarkGray
            return
        }
        Remove-NetFirewallRule -DisplayName $name
    }
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
        -Protocol $proto -LocalPort $port -Profile Any -Enabled True | Out-Null
    Write-Host "  rule '$name' dipasang ($proto $port allow)." -ForegroundColor Green
}

if (-not $NoFirewall) {
    if ($isAdmin -or $Elevated) {
        Write-Host "Menyiapkan firewall..." -ForegroundColor Cyan
        try {
            Set-RelayRule "SOCKS5 Relay $Port"     "TCP" $Port
            Set-RelayRule "SOCKS5 Relay UDP $Port" "UDP" $Port
            $off = @(Get-NetFirewallProfile | Where-Object { -not $_.Enabled })
            if ($off.Count -eq 3) {
                Write-Host "  (Windows Firewall mati total - rule ini menganggur, tidak apa-apa.)" -ForegroundColor DarkGray
            }
        } catch {
            Write-Host "  Gagal memasang rule: $_" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Tanpa hak Administrator - firewall tidak diatur." -ForegroundColor Yellow
    }
}

# --- tentukan alamat yang diiklankan ke klien untuk UDP ---------------------
function Get-PublicIp {
    foreach ($url in @("https://api.ipify.org", "https://checkip.amazonaws.com")) {
        try {
            $ip = (Invoke-RestMethod -Uri $url -TimeoutSec 5).ToString().Trim()
            if ($ip) { return $ip }
        } catch { }
    }
    return $null
}

function Test-PrivateIp([string]$ip) {
    if (-not $ip) { return $true }
    return ($ip -match '^10\.' -or $ip -match '^192\.168\.' -or $ip -match '^127\.' -or
            $ip -match '^172\.(1[6-9]|2[0-9]|3[01])\.' -or $ip -match '^169\.254\.')
}

if (-not $UdpIp) {
    $lan = (Get-NetIPConfiguration | Where-Object {
        $_.IPv4DefaultGateway -ne $null
    } | Select-Object -First 1).IPv4Address.IPAddress

    if ($Public) {
        $UdpIp = Get-PublicIp
    } elseif (Test-PrivateIp $lan) {
        # mesin ber-NAT: alamat interface tidak berguna buat klien dari internet
        $pub = Get-PublicIp
        if ($pub -and $pub -ne $lan) {
            Write-Host ""
            Write-Host "Interface $lan itu privat - memakai IP publik $pub." -ForegroundColor Yellow
            Write-Host "Kalau kliennya ada di jaringan lokal ini, jalankan dengan -UdpIp $lan." -ForegroundColor DarkGray
            $UdpIp = $pub
        } else {
            $UdpIp = $lan
        }
    } else {
        # IP publik menempel langsung di interface (RDP/VPS biasa)
        $UdpIp = $lan
    }
}
if (-not $UdpIp) {
    Write-Host "Gagal menentukan --udp-ip. Jalankan ulang dengan -UdpIp <IP>." -ForegroundColor Red
    exit 1
}

# --- sinkronkan domain DuckDNS ke alamat yang sama --------------------------
# Alamat yang dipakai klien harus sama dengan yang diiklankan untuk UDP, jadi
# domainnya diarahkan ke $UdpIp juga - bukan ke IP yang dideteksi DuckDNS sendiri.
# Gagal di sini tidak boleh menghentikan relay: cukup kembali menampilkan IP.
$clientHost = $UdpIp
if (-not $NoDuckDns) {
    $ddScript = Join-Path $PSScriptRoot "duckdns.ps1"
    if (-not (Test-Path $ddScript)) {
        Write-Host "duckdns.ps1 tidak ada - lewati update DNS." -ForegroundColor Yellow
    } else {
        Write-Host "Menyinkronkan DuckDNS..." -ForegroundColor Cyan
        & $ddScript -Ip $UdpIp
        if ($LASTEXITCODE -eq 0) {
            $ddCfg = Join-Path $PSScriptRoot "duckdns.json"
            $dom = "nifstore"
            if (Test-Path $ddCfg) {
                try {
                    $j = Get-Content $ddCfg -Raw | ConvertFrom-Json
                    if ($j.domain) { $dom = $j.domain }
                } catch { }
            }
            $clientHost = "$($dom -replace '\.duckdns\.org$', '').duckdns.org"

            # Sengaja TIDAK memasang task auto-update di sini: domainnya cuma satu,
            # jadi kalau tool ini dipakai di beberapa mesin, task di mesin yang
            # menganggur akan terus menarik domain ke dirinya sendiri. Update sekali
            # per start sudah cukup - domain ikut mesin yang sedang menjalankan relay.
            # Untuk mesin yang memang server tetap: duckdns.ps1 -Install.
        } else {
            Write-Host "Update DuckDNS gagal - klien pakai IP saja." -ForegroundColor Yellow
        }
    }
}

# --- mode multi-user: cari database bot Discord -----------------------------
# Relay membaca socksus_account/socksus_pool dari sini, jadi kredensial yang
# dijual panel !socksus langsung bisa dipakai tanpa menyalin apa pun ke sini.
$dbPath = $null
if ($MultiUser -or $Db) {
    if ($Db) {
        $dbPath = $Db
    } else {
        $dbPath = Join-Path (Split-Path $PSScriptRoot -Parent) "discord_sqlite_bot.db"
    }
    if (-not (Test-Path $dbPath)) {
        Write-Host "Database bot tidak ketemu: $dbPath" -ForegroundColor Red
        Write-Host "Jalankan dari folder tool di dalam folder bot, atau pakai -Db <path>." -ForegroundColor Yellow
        exit 1
    }
    $dbPath = (Resolve-Path $dbPath).Path
}

Write-Host ""
if ($dbPath) {
    Write-Host "Mode        : MULTI-USER (akun dari toko Discord)" -ForegroundColor Green
    Write-Host "Database    : $dbPath" -ForegroundColor DarkGray
    Write-Host "Klien pakai : ${clientHost}:${Port}:<user>:<password> (dikirim bot ke DM buyer)" -ForegroundColor Green
} else {
    Write-Host "Klien pakai : ${clientHost}:${Port}:hanif:hanif" -ForegroundColor Green
}
Write-Host "UDP diiklankan ke klien sebagai: $UdpIp" -ForegroundColor Green
if (Test-PrivateIp $UdpIp) {
    Write-Host "(alamat privat - hanya bisa dipakai klien di jaringan yang sama)" -ForegroundColor DarkGray
} else {
    Write-Host "Kalau mesin ini di belakang router, TCP DAN UDP $Port harus di-forward ke sini." -ForegroundColor Magenta
}
Write-Host ""

# --public-ip hanya mengubah tampilan log relay, bukan alamat bind atau UDP,
# jadi aman diisi nama domain. --udp-ip tetap IP: alamat itu dikirim mentah ke
# klien di balasan UDP ASSOCIATE dan banyak klien tidak mengerti tipe domain.
$pyArgs = @("socks5_relay.py", "--port", $Port, "--udp-ip", $UdpIp,
            "--public-ip", $clientHost)
if ($LocalDns) { $pyArgs += "--local-dns" }
if ($dbPath)   { $pyArgs += @("--db", $dbPath) }
python @pyArgs
