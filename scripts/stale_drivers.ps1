$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

# Third-party driver packages in the store that have a newer version of the same .inf installed.
try {
    $all = Get-WindowsDriver -Online -All
} catch {
    [pscustomobject]@{ error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 0
}

function ToVer($s) { try { [version]$s } catch { [version]'0.0' } }

$stale = $all | Group-Object { "{0}|{1}|{2}" -f (Split-Path $_.OriginalFileName -Leaf), $_.ProviderName, $_.ClassName } |
    Where-Object { $_.Count -gt 1 } | ForEach-Object {
        $sorted = $_.Group | Sort-Object @{ e = { ToVer $_.Version }; Descending = $true }, @{ e = { $_.Date }; Descending = $true }
        $newest = $sorted[0]
        $sorted | Select-Object -Skip 1 | ForEach-Object {
            [pscustomobject]@{
                published = $_.Driver
                inf       = Split-Path $_.OriginalFileName -Leaf
                provider  = $_.ProviderName
                class     = $_.ClassName
                version   = $_.Version
                date      = if ($_.Date) { ([datetime]$_.Date).ToString('yyyy-MM-dd') } else { '' }
                newer     = $newest.Version
            }
        }
    }

ConvertTo-Json -InputObject @($stale) -Depth 3 -Compress
