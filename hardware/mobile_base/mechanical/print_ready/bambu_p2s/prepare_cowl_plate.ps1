$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..\..')).Path
$bambu = 'C:\Program Files\Bambu Studio\bambu-studio.exe'
$previousPlate = Join-Path $repo 'hardware\mechanical\print_ready\bambu_p2s\Brufik_P2S_plate_01_main_0.20mm_PLA.gcode.3mf'
$inputStl = Join-Path $repo 'hardware\mobile_base\mechanical\export\N3_upper_cowl_v0_1.stl'
$settings = Join-Path $PSScriptRoot 'P2S_0.4_PLA_0.20_cowl_settings.json'
$output3mf = Join-Path $PSScriptRoot 'N3_upper_cowl_P2S_0.20mm_PLA.3mf'

foreach ($path in @($bambu, $previousPlate, $inputStl)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required file not found: $path"
    }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($previousPlate)
try {
    $entry = $archive.GetEntry('Metadata/project_settings.config')
    if ($null -eq $entry) {
        throw 'Previous plate does not contain Metadata/project_settings.config'
    }
    $reader = [System.IO.StreamReader]::new($entry.Open())
    try {
        $json = $reader.ReadToEnd()
    }
    finally {
        $reader.Dispose()
    }
}
finally {
    $archive.Dispose()
}

$json = $json.Replace('"wall_loops": "2"', '"wall_loops": "4"')
$json = $json.Replace('"sparse_infill_density": "15%"', '"sparse_infill_density": "20%"')
$json = $json.Replace('"sparse_infill_pattern": "grid"', '"sparse_infill_pattern": "gyroid"')
[System.IO.File]::WriteAllText($settings, $json, [System.Text.UTF8Encoding]::new($false))

& $bambu `
    --load-settings $settings `
    --rotate-x 180 `
    --ensure-on-bed `
    --arrange 1 `
    --slice 0 `
    --export-png 0 `
    --export-3mf $output3mf `
    $inputStl

if ($LASTEXITCODE -ne 0) {
    throw "Bambu Studio slicing failed with exit code $LASTEXITCODE"
}

Write-Host "Prepared plate: $output3mf"
