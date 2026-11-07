# Downloads a Windows ffmpeg.exe (with the LAME MP3 encoder) into vendor\ffmpeg\ffmpeg.exe
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root "vendor\ffmpeg"
$candidates = @(
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ffmpeg-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $ok = $false
    foreach ($url in $candidates) {
        for ($i = 1; $i -le 3 -and -not $ok; $i++) {
            try {
                Write-Host "Downloading $url"
                Invoke-WebRequest $url -OutFile (Join-Path $tmp "ffmpeg.zip") -UseBasicParsing -TimeoutSec 300
                $ok = $true
            } catch {
                Write-Host "  attempt $i failed: $($_.Exception.Message)"
                Start-Sleep -Seconds (5 * $i)
            }
        }
        if ($ok) { break }
    }
    if (-not $ok) { throw "Couldn't download ffmpeg. Put ffmpeg.exe in vendor\ffmpeg yourself." }
    Expand-Archive (Join-Path $tmp "ffmpeg.zip") -DestinationPath (Join-Path $tmp "x")
    $exe = Get-ChildItem (Join-Path $tmp "x") -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $exe) { throw "ffmpeg.exe not found in the download" }
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    New-Item -ItemType Directory -Path $dest | Out-Null
    Copy-Item $exe.FullName $dest
    $license = Get-ChildItem (Join-Path $tmp "x") -Recurse -Filter "LICENSE*" | Select-Object -First 1
    if ($license) { Copy-Item $license.FullName (Join-Path $dest "FFMPEG-LICENSE.txt") }
    & (Join-Path $dest "ffmpeg.exe") -hide_banner -encoders | Select-String "libmp3lame"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
