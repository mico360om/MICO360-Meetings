Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$sourcePath = Join-Path $root "assets\logo.png"
$outputPng = Join-Path $root "assets\app-icon.png"
$outputIco = Join-Path $root "assets\app-icon.ico"
$installerIco = Join-Path $root "assets\installer-icon.ico"
$uninstallerIco = Join-Path $root "assets\uninstaller-icon.ico"
$tempDir = Join-Path $root "assets\.icon-work"

function New-RoundedRectanglePath {
  param(
    [float]$X,
    [float]$Y,
    [float]$Width,
    [float]$Height,
    [float]$Radius
  )

  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $diameter = $Radius * 2
  $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
  $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
  $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
  $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
  $path.CloseFigure()
  return $path
}

function New-IconPng {
  param(
    [int]$Size,
    [string]$Path
  )

  $source = [System.Drawing.Image]::FromFile($sourcePath)
  try {
    $bitmap = New-Object System.Drawing.Bitmap $Size, $Size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
      $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
      $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
      $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
      $graphics.Clear([System.Drawing.Color]::Transparent)

      $padding = [Math]::Max(2, [Math]::Round($Size * 0.055))
      $radius = [Math]::Round($Size * 0.16)
      $card = New-RoundedRectanglePath $padding $padding ($Size - ($padding * 2)) ($Size - ($padding * 2)) $radius
      $brush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
      $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(155, 21, 24), [Math]::Max(1, $Size * 0.018))
      $graphics.FillPath($brush, $card)
      $graphics.DrawPath($pen, $card)
      $brush.Dispose()
      $pen.Dispose()

      # Crop to the compact "360 + arrow" mark so the icon remains readable at taskbar sizes.
      $cropX = [Math]::Round($source.Width * 0.37)
      $cropY = 0
      $cropW = $source.Width - $cropX
      $cropH = $source.Height
      $crop = New-Object System.Drawing.Rectangle $cropX, $cropY, $cropW, $cropH

      $maxW = $Size - ([Math]::Round($Size * 0.12) * 2)
      $maxH = $Size - ([Math]::Round($Size * 0.18) * 2)
      $scale = [Math]::Min($maxW / $cropW, $maxH / $cropH)
      $drawW = [Math]::Round($cropW * $scale)
      $drawH = [Math]::Round($cropH * $scale)
      $drawX = [Math]::Round(($Size - $drawW) / 2)
      $drawY = [Math]::Round(($Size - $drawH) / 2)
      $destination = New-Object System.Drawing.Rectangle $drawX, $drawY, $drawW, $drawH
      $graphics.DrawImage($source, $destination, $crop, [System.Drawing.GraphicsUnit]::Pixel)

      $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
      $graphics.Dispose()
      $bitmap.Dispose()
    }
  } finally {
    $source.Dispose()
  }
}

function Write-IcoFromPngs {
  param(
    [object[]]$PngPaths,
    [string]$Destination
  )

  $paths = @($PngPaths | ForEach-Object { [string]$_ })
  $pngBytes = @($paths | ForEach-Object { [System.IO.File]::ReadAllBytes($_) })
  $stream = New-Object System.IO.MemoryStream
  $writer = New-Object System.IO.BinaryWriter $stream
  try {
    $writer.Write([UInt16]0)
    $writer.Write([UInt16]1)
    $writer.Write([UInt16]$pngBytes.Count)

    $offset = 6 + (16 * $pngBytes.Count)
    for ($i = 0; $i -lt $pngBytes.Count; $i += 1) {
      $size = [int]([System.IO.Path]::GetFileNameWithoutExtension($paths[$i]) -replace '^app-icon-', '')
      $widthByte = if ($size -ge 256) { 0 } else { [byte]$size }
      $heightByte = if ($size -ge 256) { 0 } else { [byte]$size }
      $writer.Write([byte]$widthByte)
      $writer.Write([byte]$heightByte)
      $writer.Write([byte]0)
      $writer.Write([byte]0)
      $writer.Write([UInt16]1)
      $writer.Write([UInt16]32)
      $writer.Write([UInt32]$pngBytes[$i].Length)
      $writer.Write([UInt32]$offset)
      $offset += $pngBytes[$i].Length
    }

    foreach ($bytes in $pngBytes) {
      $writer.Write($bytes)
    }
    [System.IO.File]::WriteAllBytes($Destination, $stream.ToArray())
  } finally {
    $writer.Dispose()
    $stream.Dispose()
  }
}

New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
$sizes = @(16, 24, 32, 48, 64, 128, 256)
$pngs = @()
foreach ($size in $sizes) {
  $path = Join-Path $tempDir "app-icon-$size.png"
  New-IconPng -Size $size -Path $path
  $pngs += $path
}

New-IconPng -Size 1024 -Path $outputPng
Write-IcoFromPngs -PngPaths $pngs -Destination $outputIco
Copy-Item -LiteralPath $outputIco -Destination $installerIco -Force
Copy-Item -LiteralPath $outputIco -Destination $uninstallerIco -Force

Remove-Item -LiteralPath $tempDir -Recurse -Force
Write-Host "Generated app and installer icons."
