# Run in a second window while a1 runs. Samples vmmem / WSL host processes ~ every 20s. Ctrl+C to stop.
#   cd C:\Users\0\Desktop\AI\xtor-aui-spec-tools
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\win_vmmem_sample.ps1
$Out = Join-Path $PSScriptRoot "..\logs\win_vmmem_$(Get-Date -Format 'yyyyMMdd_HHmmss').csv"
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null
Add-Content $Out "time_local,process,workingset_mb,privatemb"
$MaxSamples = 2000
for ($i = 0; $i -lt $MaxSamples; $i++) {
  $t = (Get-Date).ToString("o")
  $n = 0
  Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match 'vmmem|Vmmem' -or $_.Name -like '*WSL*'
  } | ForEach-Object {
    $n++
    $ws = [math]::Round($_.WorkingSet64 / 1MB, 1)
    $pm = [math]::Round($_.PrivateMemorySize64 / 1MB, 1)
    Add-Content $Out "$t,$($_.Name),$ws,$pm"
  }
  if ($n -eq 0) { Add-Content $Out "$t,(no vmmem in Get-Process),0,0" }
  Start-Sleep -Seconds 20
}
