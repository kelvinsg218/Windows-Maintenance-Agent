#Requires -Version 5.1
<#
.SYNOPSIS
    Gera dist\maintenance.exe com PyInstaller a partir do código-fonte.

.DESCRIPTION
    O código-fonte continua sendo a fonte oficial do projeto: dist\ e build\ não são
    versionados. Ao final, o executável é validado (comando "version") e o SHA-256 é
    exibido para registro da release.

    Pré-requisito (uma vez):
        python -m venv .venv
        .\.venv\Scripts\python -m pip install -e ".[build]"

.PARAMETER Clean
    Remove build\ e dist\ antes de gerar.

.EXAMPLE
    .\scripts\build_exe.ps1 -Clean
#>
[CmdletBinding()]
param(
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Ambiente virtual não encontrado. Execute: python -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[build]"'
}
& $python -c 'import PyInstaller'
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller não está instalado. Execute: .\.venv\Scripts\python -m pip install -e ".[build]"'
}

$dist = Join-Path $root 'dist'
$work = Join-Path $root 'build'
if ($Clean) {
    foreach ($directory in @($dist, $work)) {
        if (Test-Path -LiteralPath $directory) {
            Remove-Item -LiteralPath $directory -Recurse -Force
        }
    }
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name maintenance `
    --paths (Join-Path $root 'src') `
    --distpath $dist `
    --workpath $work `
    --specpath $work `
    (Join-Path $root 'src\maintenance\__main__.py')
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falhou (código $LASTEXITCODE)."
}

Copy-Item -LiteralPath (Join-Path $root 'config.example.json') -Destination $dist -Force

$exe = Join-Path $dist 'maintenance.exe'
& $exe version
if ($LASTEXITCODE -ne 0) {
    throw 'O executável gerado não respondeu ao comando "version".'
}
$hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
Write-Host ''
Write-Host "Executável: $exe"
Write-Host "SHA-256:    $hash"
Write-Host 'Distribua maintenance.exe junto com um config.json (baseado em config.example.json).'
