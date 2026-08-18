# compare_shots.ps1 - 像素差判定两张截图（零依赖，System.Drawing）
# 用法：powershell -NoProfile -File compare_shots.ps1 <a.jpg> <b.jpg> [step]
# step = 采样步长（默认 4，即每 4 像素取 1 个比较）
param(
    [Parameter(Mandatory = $true)][string]$A,
    [Parameter(Mandatory = $true)][string]$B,
    [int]$Step = 4
)
Add-Type -AssemblyName System.Drawing
$i1 = [System.Drawing.Image]::FromFile((Resolve-Path $A))
$i2 = [System.Drawing.Image]::FromFile((Resolve-Path $B))
$w = [Math]::Min($i1.Width, $i2.Width); $h = [Math]::Min($i1.Height, $i2.Height)
$diff = 0; $total = 0
for ($x = 0; $x -lt $w; $x += $Step) { for ($y = 0; $y -lt $h; $y += $Step) {
    $total++
    if ($i1.GetPixel($x, $y) -ne $i2.GetPixel($x, $y)) { $diff++ }
}}
$i1.Dispose(); $i2.Dispose()
$pct = [Math]::Round(100.0 * $diff / $total, 1)
Write-Host "$A vs $B : $pct % 像素不同"
