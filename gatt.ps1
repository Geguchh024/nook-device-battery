# Reads the standard BLE Battery Level (service 0x180F, characteristic 0x2A19)
# of every paired BLE device that exposes it, through WinRT. Prints one JSON
# object per device: {"id","name","level","connected"}. Disconnected devices
# are skipped (reading them would only time out).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1

function Await($op, [Type]$type, [int]$ms = 5000) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    if (-not $task.Wait($ms)) { throw 'timeout' }
    $task.Result
}

$null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
$null = [Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
$null = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]

$G = 'Windows.Devices.Bluetooth.GenericAttributeProfile'
$batteryService = [Guid]'0000180f-0000-1000-8000-00805f9b34fb'
$batteryLevel = [Guid]'00002a19-0000-1000-8000-00805f9b34fb'

$selector = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService]::GetDeviceSelectorFromUuid($batteryService)
$infos = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) ([Windows.Devices.Enumeration.DeviceInformationCollection])

foreach ($info in $infos) {
    $svc = $null
    try {
        $svc = Await ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService]::FromIdAsync($info.Id)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService])
        if (-not $svc) { continue }
        $dev = $svc.Device
        if ($dev.ConnectionStatus -ne [Windows.Devices.Bluetooth.BluetoothConnectionStatus]::Connected) { continue }
        $chars = Await ($svc.GetCharacteristicsForUuidAsync($batteryLevel)) ("$G.GattCharacteristicsResult" -as [Type])
        if ($chars.Status -ne 'Success' -or $chars.Characteristics.Count -eq 0) { continue }
        $read = Await ($chars.Characteristics[0].ReadValueAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ("$G.GattReadResult" -as [Type])
        if ($read.Status -ne 'Success') { continue }
        $level = [Windows.Storage.Streams.DataReader]::FromBuffer($read.Value).ReadByte()
        @{ id = $dev.BluetoothAddress.ToString('X12'); name = $dev.Name; level = [int]$level; connected = $true } |
            ConvertTo-Json -Compress
    } catch {
        [Console]::Error.WriteLine("$($info.Name): $($_.Exception.Message)")
    } finally {
        if ($svc) { $svc.Dispose() }
    }
}
