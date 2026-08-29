[CmdletBinding()]
param(
    [string]$UploadPort = "",
    [switch]$BuildOnly,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$hardwareRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $hardwareRoot ".."))
$envPath = Join-Path $repoRoot "service\.env"
$localHeader = Join-Path $hardwareRoot "firmware\deskbot_local_config.h"

if (!(Test-Path -LiteralPath $localHeader)) {
    throw "Missing $localHeader. Copy deskbot_local_config.example.h first."
}
if (!(Test-Path -LiteralPath $envPath)) {
    throw "Missing $envPath. Copy service/.env.example to service/.env first."
}

$config = Get-Content -LiteralPath $envPath -Raw | ConvertFrom-StringData
$required = @("DOUBAO_REALTIME_APP_ID", "DOUBAO_REALTIME_ACCESS_TOKEN", "ARK_API_KEY")
$missing = @($required | Where-Object { [string]::IsNullOrWhiteSpace($config[$_]) })
if ($missing.Count -gt 0) {
    throw "Missing required local values in service/.env: $($missing -join ', ')"
}

function New-StringDefine([string]$Name, [string]$Value) {
    $escaped = $Value.Replace("\", "\\").Replace('"', '\"')
    return ('-D{0}=\"{1}\"' -f $Name, $escaped)
}

$directFlags = @(
    "-DDESKBOT_DIRECT_CLOUD=1",
    (New-StringDefine "DESKBOT_DOUBAO_APP_ID" $config["DOUBAO_REALTIME_APP_ID"]),
    (New-StringDefine "DESKBOT_DOUBAO_ACCESS_TOKEN" $config["DOUBAO_REALTIME_ACCESS_TOKEN"]),
    (New-StringDefine "DESKBOT_ARK_API_KEY" $config["ARK_API_KEY"])
) -join " "
$previousBuildFlags = $env:PLATFORMIO_BUILD_FLAGS
$env:PLATFORMIO_BUILD_FLAGS = (($previousBuildFlags, $directFlags) | Where-Object { $_ }) -join " "

$projectDir = $hardwareRoot
$createdDrive = $false
if ($hardwareRoot -match '[^\x00-\x7F]') {
    $drive = "H:"
    if (Test-Path "$drive\platformio.ini") {
        $projectDir = "$drive\"
    } elseif (Test-Path "$drive\") {
        throw "$drive is already in use. Run this script from an ASCII-only checkout or free that drive."
    } else {
        & subst.exe $drive $hardwareRoot
        if ($LASTEXITCODE -ne 0) { throw "Unable to map $hardwareRoot to $drive" }
        $createdDrive = $true
        $projectDir = "$drive\"
    }
}

try {
    $args = @("run", "-d", $projectDir, "-e", "seeed_xiao_esp32s3")
    if (!$BuildOnly) {
        $args += @("-t", "upload")
        if ($UploadPort) { $args += @("--upload-port", $UploadPort) }
    }

    $pio = Get-Command pio -ErrorAction SilentlyContinue
    if ($pio) {
        & $pio.Source @args
    } else {
        if (!$PythonPath) {
            $python = Get-Command python -ErrorAction SilentlyContinue
            if ($python) { $PythonPath = $python.Source }
        }
        if (!$PythonPath -or !(Test-Path -LiteralPath $PythonPath)) {
            throw "PlatformIO not found. Install it or pass -PythonPath to a Python environment containing platformio."
        }
        & $PythonPath -m platformio @args
    }
    if ($LASTEXITCODE -ne 0) { throw "PlatformIO failed with exit code $LASTEXITCODE" }
} finally {
    $env:PLATFORMIO_BUILD_FLAGS = $previousBuildFlags
    if ($createdDrive) { & subst.exe H: /D | Out-Null }
}
