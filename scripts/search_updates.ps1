$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

# Official driver updates for this PC via the Windows Update Agent API.
try {
    $session  = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result   = $searcher.Search("IsInstalled=0 and Type='Driver' and IsHidden=0")
} catch {
    [pscustomobject]@{ error = $_.Exception.Message; updates = @() } | ConvertTo-Json -Compress
    exit 0
}

$list = foreach ($u in $result.Updates) {
    [pscustomobject]@{
        id           = $u.Identity.UpdateID
        title        = $u.Title
        model        = $u.DriverModel
        manufacturer = $u.DriverManufacturer
        class        = $u.DriverClass
        date         = if ($u.DriverVerDate) { ([datetime]$u.DriverVerDate).ToString('yyyy-MM-dd') } else { '' }
        sizeMB       = [math]::Round($u.MaxDownloadSize / 1MB, 1)
        hwid         = [string]$u.DriverHardwareID
    }
}

[pscustomobject]@{ error = $null; updates = @($list) } | ConvertTo-Json -Depth 4 -Compress
