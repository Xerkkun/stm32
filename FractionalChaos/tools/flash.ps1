[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('f746', 'h755')]
    [string]$Board,

    [Parameter(Mandatory = $true)]
    [ValidateSet('lorenz', 'rossler', 'chen')]
    [string]$System,

    [Parameter(Mandatory = $true)]
    [ValidateSet('efork3', 'gl')]
    [string]$Method,

    [string]$ProbeSerial = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (
    Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'Resolve-Stm32Tools.ps1')
$tools = Resolve-Stm32Tools

$connection = @('port=SWD', 'mode=UR', 'reset=HWrst')
if ($ProbeSerial) {
    $connection += "sn=$ProbeSerial"
}

if ($Board -eq 'f746') {
    $elf = Join-Path $projectRoot (
        "build\f746-release\f746_${System}_${Method}.elf")
    if (-not (Test-Path -LiteralPath $elf -PathType Leaf)) {
        throw "No existe $elf. Ejecuta primero la tarea build:f746."
    }

    & $tools.Programmer -c @connection -w $elf -v -rst
    if ($LASTEXITCODE -ne 0) {
        throw 'Falló la programación de la NUCLEO-F746ZG.'
    }
    exit 0
}

$cm7Elf = Join-Path $projectRoot (
    "build\h755-release\h755_m7_${System}_${Method}.elf")
$cm4Elf = Join-Path $projectRoot (
    'build\h755-release\h755_m4_uart.elf')

foreach ($elf in @($cm7Elf, $cm4Elf)) {
    if (-not (Test-Path -LiteralPath $elf -PathType Leaf)) {
        throw "No existe $elf. Ejecuta primero la tarea build:h755."
    }
}

Write-Host 'Programando Cortex-M7 en Flash bank 1...'
& $tools.Programmer -c @connection -halt -w $cm7Elf -v
if ($LASTEXITCODE -ne 0) {
    throw 'Falló la programación del Cortex-M7.'
}

Write-Host 'Programando Cortex-M4 en Flash bank 2...'
& $tools.Programmer -c @connection -halt -w $cm4Elf -v -rst
if ($LASTEXITCODE -ne 0) {
    throw 'Falló la programación del Cortex-M4.'
}
