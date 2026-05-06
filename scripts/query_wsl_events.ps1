# Quick scan: WSL-related Windows events (requires read access to logs)
$since = (Get-Date).AddDays(-5)
Write-Host "=== System: Error+Warning (last 5d, max 20) ==="
try {
  Get-WinEvent -FilterHashtable @{
    LogName   = 'System'
    Level     = 1,2,3
    StartTime = $since
  } -MaxEvents 20 -ErrorAction Stop | ForEach-Object {
    $m = if ($null -ne $_.Message) { $_.Message } else { '' }
    if ($m.Length -gt 160) { $m = $m.Substring(0, 160) + '...' }
    "{0} [Id={1}] {2}" -f $_.TimeCreated, $_.Id, $m
  }
} catch { "read failed: $($_.Exception.Message)" }

Write-Host ""
Write-Host "=== Keyword: Wsl Lxss Vmmem (last 5d, System, max 30) ==="
try {
  Get-WinEvent -FilterHashtable @{
    LogName   = 'System'
    StartTime = $since
  } -MaxEvents 800 -ErrorAction Stop |
    Where-Object { $_.Message -match 'Wsl|WSL|Lxss|Vmmem|vmmem' } |
    Select-Object -First 30 | ForEach-Object {
      $m = $_.Message
      if ($m.Length -gt 120) { $m = $m.Substring(0, 120) + '...' }
      "{0} [Id={1}] {2}" -f $_.TimeCreated, $_.Id, $m
    }
} catch { "read failed: $($_.Exception.Message)" }

Write-Host ""
$lxss = 'Microsoft-Windows-LxssManager/Operational'
if (Get-WinEvent -ListLog $lxss -ErrorAction SilentlyContinue) {
  Write-Host "=== $lxss (max 15) ==="
  Get-WinEvent -LogName $lxss -MaxEvents 15 -ErrorAction SilentlyContinue | ForEach-Object {
    "{0} [Id={1}] {2}" -f $_.TimeCreated, $_.Id, ($_.Message -replace "\s+"," ")
  }
} else { Write-Host "No LxssManager/Operational channel (common)." }
