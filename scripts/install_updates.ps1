param(
    [Parameter(Mandatory)][string]$Ids,          # comma-separated UpdateIDs
    [switch]$RestorePoint
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Say($m) { Write-Output "LOG: $m" }

$wanted = $Ids -split ',' | Where-Object { $_ }

if ($RestorePoint) {
    try {
        Say 'Creating restore point...'
        Enable-ComputerRestore -Drive "$env:SystemDrive\" -ErrorAction SilentlyContinue
        Checkpoint-Computer -Description 'DriverTool: before driver install' -RestorePointType MODIFY_SETTINGS
        Say 'Restore point created.'
    } catch {
        Say "Restore point skipped: $($_.Exception.Message)"
    }
}

$session  = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$found    = $searcher.Search("IsInstalled=0 and Type='Driver' and IsHidden=0")

$toGet = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $found.Updates) {
    if ($wanted -contains $u.Identity.UpdateID) {
        if (-not $u.EulaAccepted) { $u.AcceptEula() }
        [void]$toGet.Add($u)
    }
}
if ($toGet.Count -eq 0) { Say 'Nothing to install (updates no longer offered).'; Write-Output 'RESULT: {"installed":0,"failed":0,"reboot":false}'; exit 0 }

Say "Downloading $($toGet.Count) driver(s)..."
$dl = $session.CreateUpdateDownloader()
$dl.Updates = $toGet
[void]$dl.Download()

$ready = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $toGet) { if ($u.IsDownloaded) { [void]$ready.Add($u) } else { Say "Download failed: $($u.Title)" } }

Say "Installing $($ready.Count) driver(s)..."
$inst = $session.CreateUpdateInstaller()
$inst.Updates = $ready
$res = $inst.Install()

$ok = 0; $bad = 0
for ($i = 0; $i -lt $ready.Count; $i++) {
    $r = $res.GetUpdateResult($i)
    # ResultCode: 2 = Succeeded, 3 = SucceededWithErrors
    if ($r.ResultCode -in 2, 3) { $ok++; Say "OK: $($ready.Item($i).Title)" }
    else { $bad++; Say ("FAIL (0x{0:X}): {1}" -f $r.HResult, $ready.Item($i).Title) }
}
$reboot = [bool]$res.RebootRequired
Write-Output ("RESULT: " + (@{ installed = $ok; failed = $bad; reboot = $reboot } | ConvertTo-Json -Compress))
