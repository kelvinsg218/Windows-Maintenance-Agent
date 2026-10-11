#Requires -Version 5.1
<#
.SYNOPSIS
    Gera os executáveis do Windows Maintenance Agent com PyInstaller.

.DESCRIPTION
    - dist\Windows-Maintenance-Agent-v<versão>.exe: aplicativo desktop (janela própria, sem
      console), aberto com dois cliques.
    - dist\maintenance.exe: linha de comando (status, check, run...), usada por técnicos e
      pelo Agendador de Tarefas.

    Os dois são arquivos únicos e independentes (não exigem Python), com ícone e informações
    de versão, e são executados sem pedir privilégios de administrador (manifesto asInvoker).
    O código-fonte continua sendo a fonte oficial: dist\ e build\ não são versionados.
    Ao final, os executáveis são validados e o SHA-256 é gravado em dist\SHA256SUMS.txt.

    Pré-requisito (uma vez):
        python -m venv .venv
        .\.venv\Scripts\python -m pip install -e ".[build]"

.PARAMETER Clean
    Remove build\ e dist\ antes de gerar.

.PARAMETER Target
    All (padrão), Gui (somente o aplicativo desktop) ou Cli (somente maintenance.exe).

.EXAMPLE
    .\scripts\build_exe.ps1 -Clean
#>
[CmdletBinding()]
param(
    [switch]$Clean,

    [ValidateSet('All', 'Gui', 'Cli')]
    [string]$Target = 'All'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root 'src'
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Ambiente virtual não encontrado. Execute: python -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[build]"'
}
& $python -c 'import PyInstaller'
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller não está instalado. Execute: .\.venv\Scripts\python -m pip install -e ".[build]"'
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $src
try {
    $version = (& $python -c 'import maintenance; print(maintenance.__version__)').Trim()
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Versão inválida em maintenance.__version__: '$version'"
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
New-Item -ItemType Directory -Force -Path $dist, $work | Out-Null

$assets = Join-Path $src 'maintenance\gui\assets'
$icon = Join-Path $assets 'app.ico'
$logo = Join-Path $assets 'logo.png'
$guiName = "Windows-Maintenance-Agent-v$version"

function New-VersionFile([string]$path, [string]$description, [string]$fileName) {
    # Informações exibidas em Propriedades > Detalhes e no Gerenciador de Tarefas.
    $numbers = ($version.Split('.') + '0') -join ', '
    $content = @"
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($numbers),
    prodvers=($numbers),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('041604B0', [
        StringStruct('FileDescription', '$description'),
        StringStruct('FileVersion', '$version'),
        StringStruct('InternalName', 'WindowsMaintenanceAgent'),
        StringStruct('OriginalFilename', '$fileName'),
        StringStruct('ProductName', 'Windows Maintenance Agent'),
        StringStruct('ProductVersion', '$version')])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"@
    [System.IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-PyInstaller([string]$name, [string]$entry, [string[]]$extra) {
    $specDir = Join-Path $work $name
    New-Item -ItemType Directory -Force -Path $specDir | Out-Null
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --noupx `
        --name $name `
        --icon $icon `
        --paths $src `
        --distpath $dist `
        --workpath $specDir `
        --specpath $specDir `
        @extra `
        $entry
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller falhou ao gerar $name (código $LASTEXITCODE)."
    }
}

function Test-WindowsSubsystem([string]$path) {
    # Subsystem do cabeçalho PE: 2 = aplicativo de janela (sem console), 3 = console.
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $peOffset = [BitConverter]::ToInt32($bytes, 0x3C)
    return [BitConverter]::ToUInt16($bytes, $peOffset + 24 + 68)
}

function Test-AsInvoker([string]$path) {
    $text = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes($path))
    return $text -match 'requestedExecutionLevel\s+level="asInvoker"'
}

$built = @()

if ($Target -in @('All', 'Gui')) {
    $versionFile = Join-Path $work 'version_gui.txt'
    New-VersionFile $versionFile 'Windows Maintenance Agent' "$guiName.exe"
    Invoke-PyInstaller $guiName (Join-Path $src 'maintenance\gui\__main__.py') @(
        '--windowed',
        '--version-file', $versionFile,
        '--add-data', "$icon;maintenance\gui\assets",
        '--add-data', "$logo;maintenance\gui\assets"
    )
    $guiExe = Join-Path $dist "$guiName.exe"
    if ((Test-WindowsSubsystem $guiExe) -ne 2) {
        throw 'O aplicativo desktop foi gerado como programa de console.'
    }
    if ((Get-Item -LiteralPath $guiExe).VersionInfo.ProductVersion -ne $version) {
        throw 'Informações de versão ausentes no aplicativo desktop.'
    }
    if (-not (Test-AsInvoker $guiExe)) {
        throw 'O manifesto do aplicativo desktop não usa requestedExecutionLevel asInvoker.'
    }
    $built += $guiExe
}

if ($Target -in @('All', 'Cli')) {
    $versionFile = Join-Path $work 'version_cli.txt'
    New-VersionFile $versionFile 'Windows Maintenance Agent (linha de comando)' 'maintenance.exe'
    Invoke-PyInstaller 'maintenance' (Join-Path $src 'maintenance\__main__.py') @(
        '--console',
        '--version-file', $versionFile
    )
    $cliExe = Join-Path $dist 'maintenance.exe'
    $output = & $cliExe version
    if ($LASTEXITCODE -ne 0 -or $output -ne "Windows Maintenance Agent v$version") {
        throw 'O executável maintenance.exe não respondeu corretamente ao comando "version".'
    }
    Copy-Item -LiteralPath (Join-Path $root 'config.example.json') -Destination $dist -Force
    $built += $cliExe
}

$sums = foreach ($file in $built) {
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $(Split-Path -Leaf $file)"
}
$sumsFile = Join-Path $dist 'SHA256SUMS.txt'
[System.IO.File]::WriteAllLines($sumsFile, [string[]]$sums, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ''
Write-Host "Windows Maintenance Agent v$version"
foreach ($file in $built) {
    $size = (Get-Item -LiteralPath $file).Length / 1MB
    Write-Host ("  {0}  ({1:N1} MB)" -f $file, $size)
}
Write-Host "  $sumsFile"
Get-Content -LiteralPath $sumsFile | ForEach-Object { Write-Host "    $_" }
