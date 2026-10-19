$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

# Installed drivers
$drivers = Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName } | ForEach-Object {
    [pscustomobject]@{
        name     = $_.DeviceName
        class    = $_.DeviceClass
        vendor   = $_.Manufacturer
        provider = $_.DriverProviderName
        version  = $_.DriverVersion
        date     = if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' }
        inf      = $_.InfName
        signed   = [bool]$_.IsSigned
        deviceId = $_.DeviceID
        hwid     = @($_.HardWareID)
    }
}

# Devices with problems (missing / failed drivers)
$problems = Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 } | ForEach-Object {
    [pscustomobject]@{
        name     = if ($_.Name) { $_.Name } else { 'Unknown device' }
        code     = [int]$_.ConfigManagerErrorCode
        deviceId = $_.PNPDeviceID
        hwid     = @($_.HardwareID)
    }
}

[pscustomobject]@{
    drivers  = @($drivers)
    problems = @($problems)
} | ConvertTo-Json -Depth 5 -Compress
