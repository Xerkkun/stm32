<#
.SYNOPSIS
Lee y valida los registros FRP1 al terminar un benchmark físico.

.DESCRIPTION
Usa HOTPLUG para no reiniciar la placa. El archivo ELF fija la dirección y el
tamaño exactos del registro; para CM4 traduce el alias 0x10000000 de SRAM D2 a
su dirección física 0x30000000. Una localización de símbolo o un dry-run no es
evidencia física.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('f746', 'h755')]
    [string]$Board,

    [Parameter(Mandatory = $true)]
    [string]$BuildDirectory,

    [Parameter(Mandatory = $true)]
    [string]$Target,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{16,64}$')]
    [string]$ProbeSerial,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable,

    [ValidateRange(1, 30)]
    [int]$TimeoutSeconds = 5,

    [switch]$AllowUnavailablePostPowerCycle
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$resolvedBuild = (Resolve-Path -LiteralPath $BuildDirectory).Path
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
$allowedBuildRoot = [IO.Path]::GetFullPath(
    (Join-Path $projectRoot 'build'))
$allowedOutputRoot = [IO.Path]::GetFullPath(
    (Join-Path $projectRoot 'validation\results'))

if (-not $resolvedBuild.StartsWith(
        $allowedBuildRoot,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "BuildDirectory debe permanecer dentro de $allowedBuildRoot."
}
if (-not $resolvedOutput.StartsWith(
        $allowedOutputRoot,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory debe permanecer dentro de $allowedOutputRoot."
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "No existe PythonExecutable: $PythonExecutable"
}

. (Join-Path $PSScriptRoot 'Resolve-Stm32Tools.ps1')
$tools = Resolve-Stm32Tools
$nm = Join-Path $tools.GccBin 'arm-none-eabi-nm.exe'
if (-not (Test-Path -LiteralPath $nm -PathType Leaf)) {
    throw "No existe arm-none-eabi-nm: $nm"
}

if (-not (Test-Path -LiteralPath $resolvedOutput -PathType Container)) {
    New-Item -ItemType Directory -Path $resolvedOutput | Out-Null
}

$records = if ($Board -eq 'f746') {
    @(
        [pscustomobject]@{
            Core = 'f746_m7'
            Elf = Join-Path $resolvedBuild "$Target.elf"
        }
    )
}
else {
    @(
        [pscustomobject]@{
            Core = 'h755_m7'
            Elf = Join-Path $resolvedBuild "$Target.elf"
        },
        [pscustomobject]@{
            Core = 'h755_m4'
            Elf = Join-Path $resolvedBuild 'h755_m4_uart.elf'
        }
    )
}

foreach ($record in $records) {
    if (-not (Test-Path -LiteralPath $record.Elf -PathType Leaf)) {
        throw "No existe ELF para $($record.Core): $($record.Elf)"
    }
    $locationPath = Join-Path $resolvedOutput (
        "runtime_probe_$($record.Core)_location.json")
    $rawPath = Join-Path $resolvedOutput (
        "runtime_probe_$($record.Core).bin")
    $reportPath = Join-Path $resolvedOutput (
        "runtime_probe_$($record.Core).json")
    foreach ($path in @($locationPath, $rawPath, $reportPath)) {
        if (Test-Path -LiteralPath $path) {
            throw "No se sobrescribe artefacto FRP1 existente: $path"
        }
    }

    & $PythonExecutable (
        Join-Path $projectRoot 'validation\runtime_probe.py') `
        locate `
        --elf $record.Elf `
        --nm $nm `
        --core $record.Core `
        --output $locationPath
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo localizar FRP1 para $($record.Core)."
    }
    $location = Get-Content -LiteralPath $locationPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if ($location.status -ne 'prepared_symbol_location_not_measurement' -or
        [int]$location.size -ne 84) {
        throw "Localización FRP1 inválida para $($record.Core)."
    }

    $address = '0x{0:X8}' -f [uint32]$location.physical_debug_address
    if (-not $PSCmdlet.ShouldProcess(
            "$Board/$($record.Core)/$ProbeSerial",
            "Leer 84 bytes FRP1 en $address mediante HOTPLUG")) {
        continue
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastDecode = $null
    do {
        & $tools.Programmer `
            -c port=SWD "sn=$ProbeSerial" mode=HOTPLUG `
            -u $address 84 $rawPath
        if ($LASTEXITCODE -eq 0) {
            & $PythonExecutable (
                Join-Path $projectRoot 'validation\runtime_probe.py') `
                decode `
                --input $rawPath `
                --elf $record.Elf `
                --probe-serial $ProbeSerial `
                --output $reportPath
            $lastDecode = $LASTEXITCODE
            if ($lastDecode -eq 0) {
                break
            }
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)

    if ($lastDecode -ne 0 -or
        -not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        if (-not $AllowUnavailablePostPowerCycle) {
            throw "FRP1 no quedó completo y válido para $($record.Core)."
        }
        $partial = [ordered]@{
            schema                       = 'fractional-chaos-runtime-probe-unavailable-v1'
            status                       = 'swd_unavailable_post_power_cycle'
            core                         = $record.Core
            board                        = $Board
            reason                       = 'HOTPLUG did not return a complete FRP1 record within the post-power-cycle timeout.'
            eligible_as_primary_evidence = $false
        } | ConvertTo-Json -Depth 3
        $partial | Set-Content -LiteralPath $reportPath -Encoding UTF8
        Write-Warning "FRP1 no pudo leerse para $($record.Core) tras el ciclo de alimentación; se registra el estado explícito."
    }
}

