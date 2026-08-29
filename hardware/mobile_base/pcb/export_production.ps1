$ErrorActionPreference = 'Stop'

$kicadCli = 'C:\Users\Chen\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe'
$board = Join-Path $PSScriptRoot 'BrufikMobileBase.kicad_pcb'
$production = Join-Path $PSScriptRoot 'production'
$gerbers = Join-Path $production 'gerbers'

New-Item -ItemType Directory -Force -Path $production | Out-Null
New-Item -ItemType Directory -Force -Path $gerbers | Out-Null

Get-ChildItem -LiteralPath $gerbers -File | Remove-Item -Force

& $kicadCli pcb drc --severity-all --format json `
  --output (Join-Path $production 'drc.json') $board

& $kicadCli pcb export gerbers `
  --output $gerbers `
  --layers 'F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts' `
  --subtract-soldermask --check-zones $board

& $kicadCli pcb export drill `
  --output $gerbers `
  --format excellon --excellon-units mm --excellon-separate-th `
  --generate-map --map-format gerberx2 --generate-report `
  --report-path (Join-Path $gerbers 'drill_report.txt') $board

& $kicadCli pcb export pos `
  --output (Join-Path $production 'BrufikMobileBase_positions.csv') `
  --format csv --units mm --side both $board

& $kicadCli pcb render `
  --output (Join-Path $production 'pcb_top.png') `
  --width 1600 --height 1200 --side top --background opaque --quality high $board

$zip = Join-Path $production 'BrufikMobileBase_gerbers.zip'
if (Test-Path -LiteralPath $zip) {
  Remove-Item -LiteralPath $zip -Force
}
Compress-Archive -Path (Join-Path $gerbers '*') -DestinationPath $zip

Write-Host "Production package: $zip"
