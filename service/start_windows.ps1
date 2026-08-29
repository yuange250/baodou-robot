$ErrorActionPreference = "Stop"

$serviceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$drive = "R:"
if (-not (Test-Path "R:\")) {
    subst $drive $serviceRoot
} elseif (-not (Test-Path "R:\config.yaml") -or -not (Test-Path "R:\.venv\Scripts\python.exe")) {
    throw "R: is already in use by another program."
}

$env:PYTHONIOENCODING = "utf-8"
$env:ASR_MODEL_DIR = "R:\models\SenseVoiceSmall"
$env:CAMERA_FACE_LANDMARKER_PATH = "R:\models\mediapipe\face_landmarker.task"
$env:DESKBOT_SERVER_CONFIG = "R:\config.yaml"
$opusDllDir = "R:\.venv\Lib\site-packages\av.libs"
$env:Path = "$opusDllDir;R:\.venv\Scripts;$env:Path"

Set-Location "R:\"
& "R:\.venv\Scripts\python.exe" -m deskbot_server
exit $LASTEXITCODE
