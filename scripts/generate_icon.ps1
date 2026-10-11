#Requires -Version 5.1
<#
.SYNOPSIS
    Gera o ícone do aplicativo (app.ico) e o logotipo da barra lateral (logo.png).

.DESCRIPTION
    O desenho é vetorial (GDI+): um escudo verde com marca de verificação sobre um bloco
    grafite, renderizado separadamente em cada tamanho do .ico para ficar nítido.
    Os arquivos gerados são versionados em src\maintenance\gui\assets; este script só
    precisa ser executado novamente se o desenho mudar.

.EXAMPLE
    .\scripts\generate_icon.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent $PSScriptRoot
$assets = Join-Path $root 'src\maintenance\gui\assets'
New-Item -ItemType Directory -Force -Path $assets | Out-Null

function New-RoundedRectangle([single]$x, [single]$y, [single]$w, [single]$h, [single]$r) {
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = $r * 2
    $path.AddArc($x, $y, $d, $d, 180, 90)
    $path.AddArc($x + $w - $d, $y, $d, $d, 270, 90)
    $path.AddArc($x + $w - $d, $y + $h - $d, $d, $d, 0, 90)
    $path.AddArc($x, $y + $h - $d, $d, $d, 90, 90)
    $path.CloseFigure()
    return $path
}

function New-IconPng([int]$size) {
    $bitmap = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bitmap)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)
    $g.ScaleTransform($size / 256.0, $size / 256.0)

    # Bloco grafite com borda sutil.
    $tile = New-RoundedRectangle 12 12 232 232 50
    $tileBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        (New-Object System.Drawing.PointF(0, 12)), (New-Object System.Drawing.PointF(0, 244)),
        [System.Drawing.ColorTranslator]::FromHtml('#2b3038'),
        [System.Drawing.ColorTranslator]::FromHtml('#121418'))
    $g.FillPath($tileBrush, $tile)
    $border = New-Object System.Drawing.Pen([System.Drawing.ColorTranslator]::FromHtml('#3d434c'), 4)
    $g.DrawPath($border, $tile)

    # Escudo.
    $shield = New-Object System.Drawing.Drawing2D.GraphicsPath
    $shield.AddBezier(70, 74, 96, 70, 114, 62, 128, 50)
    $shield.AddBezier(128, 50, 142, 62, 160, 70, 186, 74)
    $shield.AddLine(186, 74, 186, 126)
    $shield.AddBezier(186, 126, 186, 166, 162, 192, 128, 210)
    $shield.AddBezier(128, 210, 94, 192, 70, 166, 70, 126)
    $shield.CloseFigure()
    $shieldBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        (New-Object System.Drawing.PointF(0, 50)), (New-Object System.Drawing.PointF(0, 210)),
        [System.Drawing.ColorTranslator]::FromHtml('#46c35a'),
        [System.Drawing.ColorTranslator]::FromHtml('#1f7f35'))
    $g.FillPath($shieldBrush, $shield)

    # Marca de verificação.
    $check = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 20)
    $check.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $check.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $check.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $points = [System.Drawing.PointF[]]@(
        (New-Object System.Drawing.PointF(99, 130)),
        (New-Object System.Drawing.PointF(121, 152)),
        (New-Object System.Drawing.PointF(159, 108))
    )
    $g.DrawLines($check, $points)

    $stream = New-Object System.IO.MemoryStream
    $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    foreach ($item in @($check, $shieldBrush, $shield, $border, $tileBrush, $tile, $g, $bitmap)) {
        $item.Dispose()
    }
    return , $stream.ToArray()
}

# .ico com imagens PNG (suportado pelo Windows Vista e posteriores).
$sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
$images = @{}
foreach ($size in $sizes) { $images[$size] = New-IconPng $size }

$ico = New-Object System.IO.MemoryStream
$writer = New-Object System.IO.BinaryWriter($ico)
$writer.Write([UInt16]0)
$writer.Write([UInt16]1)
$writer.Write([UInt16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
foreach ($size in $sizes) {
    $dimension = if ($size -ge 256) { 0 } else { $size }
    $writer.Write([Byte]$dimension)
    $writer.Write([Byte]$dimension)
    $writer.Write([Byte]0)
    $writer.Write([Byte]0)
    $writer.Write([UInt16]1)
    $writer.Write([UInt16]32)
    $writer.Write([UInt32]$images[$size].Length)
    $writer.Write([UInt32]$offset)
    $offset += $images[$size].Length
}
foreach ($size in $sizes) { $writer.Write($images[$size]) }
$writer.Flush()

$icoPath = Join-Path $assets 'app.ico'
$logoPath = Join-Path $assets 'logo.png'
[System.IO.File]::WriteAllBytes($icoPath, $ico.ToArray())
[System.IO.File]::WriteAllBytes($logoPath, (New-IconPng 68))
$writer.Dispose()
Write-Host "Gerado: $icoPath"
Write-Host "Gerado: $logoPath"
