param(
    [string]$ListenAddress = "",
    [int]$Port = 8000,
    [string]$WslVenv = "~/.venvs/stackchan68",
    [switch]$StopExisting
)

$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Get-DefaultListenAddress {
    $candidates = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.InterfaceAlias -notmatch "vEthernet|VirtualBox|Loopback|VMware|Hyper-V"
        } |
        Sort-Object @{ Expression = { if ($_.IPAddress -like "192.168.*") { 0 } elseif ($_.IPAddress -like "10.*") { 1 } else { 2 } } }
    if (-not $candidates) {
        throw "No LAN IPv4 address found. Pass -ListenAddress explicitly."
    }
    return $candidates[0].IPAddress
}

function Convert-ToWslPath([string]$Path) {
    $escaped = $Path.Replace("\", "\\")
    $result = & wsl.exe -e bash -lc "wslpath -a '$escaped'"
    if ($LASTEXITCODE -ne 0 -or -not $result) {
        throw "wslpath failed for $Path"
    }
    return ($result | Select-Object -First 1)
}

function Quote-BashPath([string]$Path) {
    if ($Path.StartsWith("~/")) {
        return "~/" + (($Path.Substring(2)).Replace("'", "'\''"))
    }
    return "'" + $Path.Replace("'", "'\''") + "'"
}

$repoRoot = Get-RepoRoot
$serverDir = Join-Path $repoRoot "server"
$proxyScript = Join-Path $repoRoot "scripts\stackchan_lan_proxy.py"
$serverLogOut = Join-Path $serverDir "server.out.log"
$serverLogErr = Join-Path $serverDir "server.err.log"
$proxyLog = Join-Path $serverDir "lan_proxy.log"

if (-not $ListenAddress) {
    $ListenAddress = Get-DefaultListenAddress
}

if ($StopExisting) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -eq $ListenAddress } |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
    & wsl.exe -e bash -lc "pkill -f 'python -m uvicorn main:app' || true"
    Start-Sleep -Seconds 1
}

$wslServerDir = Convert-ToWslPath $serverDir
$wslOut = Convert-ToWslPath $serverLogOut
$wslErr = Convert-ToWslPath $serverLogErr
$venvActivate = Quote-BashPath (($WslVenv.TrimEnd([char[]]@("/", "\"))) + "/bin/activate")
$runtimeEnv = "export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1"
$uvicornCmd = "cd '$wslServerDir' && source $venvActivate && $runtimeEnv && exec python -m uvicorn main:app --host 0.0.0.0 --port $Port > '$wslOut' 2> '$wslErr'"
$serverPs = "wsl.exe --cd '$wslServerDir' -e bash -lc `"$uvicornCmd`""
$serverEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($serverPs))
$serverProc = Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile", "-EncodedCommand", $serverEncoded) -WindowStyle Hidden -PassThru

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $pythonw) {
    $pythonw = (Get-Command python.exe -ErrorAction Stop | Select-Object -First 1).Source
}
$proxyProc = Start-Process -FilePath $pythonw -ArgumentList @(
    $proxyScript,
    "--listen-address", $ListenAddress,
    "--listen-port", "$Port",
    "--target-host", "127.0.0.1",
    "--target-port", "$Port",
    "--log", $proxyLog
) -WindowStyle Hidden -PassThru

$readyUrl = "http://${ListenAddress}:$Port/ready"
$deadline = (Get-Date).AddMinutes(3)
$ready = $false
do {
    Start-Sleep -Seconds 3
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $readyUrl -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        # Startup loads Whisper/TTS lazily; keep polling until deadline.
    }
} while ((Get-Date) -lt $deadline)

[PSCustomObject]@{
    Ready = $ready
    ReadyUrl = $readyUrl
    ServerLauncherPid = $serverProc.Id
    ProxyPid = $proxyProc.Id
    ListenAddress = $ListenAddress
    Port = $Port
    ServerOutLog = $serverLogOut
    ServerErrLog = $serverLogErr
    ProxyLog = $proxyLog
}
