param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$source = Join-Path $PSScriptRoot "kudog_native.c"
if (-not (Test-Path $source)) {
    throw "native source not found: $source"
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repoRoot "assets\kudog_native.dll"
}

$outputDir = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force $outputDir | Out-Null

$gcc = Get-Command gcc -ErrorAction SilentlyContinue
if ($gcc) {
    & $gcc.Source `
        -shared `
        -O3 `
        -std=c11 `
        -s `
        -o $OutputPath `
        $source
    Write-Host "built with gcc: $OutputPath"
    exit 0
}

$cl = Get-Command cl -ErrorAction SilentlyContinue
if ($cl) {
    $tempObj = Join-Path $outputDir "kudog_native.obj"
    $dllArgs = @(
        "/nologo",
        "/LD",
        "/O2",
        "/TC",
        "/Fe:$OutputPath",
        "/Fo$tempObj",
        $source
    )
    & $cl.Source @dllArgs
    if ($LASTEXITCODE -ne 0) {
        throw "cl failed with exit code $LASTEXITCODE"
    }
    if (Test-Path $tempObj) {
        Remove-Item $tempObj -Force -ErrorAction SilentlyContinue
    }
    Write-Host "built with cl: $OutputPath"
    exit 0
}

throw "neither gcc nor cl was found in PATH"
