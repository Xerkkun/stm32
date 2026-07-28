<#
.SYNOPSIS
Programa una placa STM32 seleccionada de forma explícita por número de serie.

.DESCRIPTION
Antes de cualquier escritura, consulta las sondas ST-LINK conectadas y exige
que ProbeSerial identifique exactamente una sonda cuyo Board Name corresponda
con Board. El script se detiene si falta el serial, no existe o pertenece a
otra placa.

.PARAMETER ProbeSerial
Número de serie ST-LINK completo mostrado por
STM32_Programmer_CLI.exe -l stlink-only. Es obligatorio incluso cuando sólo
hay una sonda conectada.

.PARAMETER BuildDirectory
Directorio CMake que contiene los HEX. Si se omite conserva la ruta histórica
build/<board>-release. Debe permanecer dentro de build/.

.PARAMETER ResetOnly
Valida serial y Board Name y realiza únicamente un reset hardware. No resuelve
ni escribe imágenes.

.PARAMETER NoReset
Programa y verifica las imágenes, pero conserva el objetivo detenido. Se usa
para abrir y vaciar el puerto serie antes de aplicar un reset separado.

.EXAMPLE
.\tools\flash.ps1 -Board h755 -System lorenz -Method m2sfrk `
  -ProbeSerial 003700344142501220353451

.EXAMPLE
.\tools\flash.ps1 -Board f746 -System lorenz -Method efork3 `
  -ProbeSerial 066AFF504955657867165348 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('f746', 'h755')]
    [string]$Board,

    [Parameter(Mandatory = $true)]
    [ValidateSet('lorenz', 'rossler', 'chen')]
    [string]$System,

    [Parameter(Mandatory = $true)]
    [ValidateSet('efork3', 'gl', 'm2sfrk')]
    [string]$Method,

    [ValidateSet('float32', 'fixed')]
    [string]$Representation = 'float32',

    [string]$ProbeSerial = '',

    [string]$BuildDirectory = '',

    [switch]$ResetOnly,

    [switch]$NoReset
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ProbeSerial)) {
    throw (
        'ProbeSerial es obligatorio. Consulte las sondas con ' +
        'STM32_Programmer_CLI.exe -l stlink-only y vuelva a ejecutar el ' +
        'comando con -ProbeSerial <ST-LINK_SN>.')
}
if ($ResetOnly -and $NoReset) {
    throw 'ResetOnly y NoReset son mutuamente excluyentes.'
}

$normalizedProbeSerial = $ProbeSerial.Trim()
if ($normalizedProbeSerial -notmatch '^[0-9A-Fa-f]+$') {
    throw (
        "ProbeSerial '$ProbeSerial' no es un número de serie ST-LINK válido.")
}

$projectRoot = (Resolve-Path (
    Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'Resolve-Stm32Tools.ps1')
$tools = Resolve-Stm32Tools

$allowedBuildRoot = [IO.Path]::GetFullPath((
    Join-Path $projectRoot 'build'))
$allowedBuildPrefix = $allowedBuildRoot.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar
if ([string]::IsNullOrWhiteSpace($BuildDirectory)) {
    $resolvedBuildDirectory = [IO.Path]::GetFullPath((
        Join-Path $projectRoot "build\$Board-release"))
}
else {
    $candidateBuildDirectory = if (
        [IO.Path]::IsPathRooted($BuildDirectory)) {
        $BuildDirectory
    }
    else {
        Join-Path $projectRoot $BuildDirectory
    }
    $resolvedBuildDirectory = [IO.Path]::GetFullPath(
        $candidateBuildDirectory)
}
if (-not $resolvedBuildDirectory.StartsWith(
        $allowedBuildPrefix,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw (
        "BuildDirectory debe permanecer dentro de $allowedBuildRoot; " +
        "se recibió $resolvedBuildDirectory.")
}

function Get-ConnectedStLinkProbes {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Programmer
    )

    $listLines = @(& $Programmer -l stlink-only 2>&1)
    $listExitCode = $LASTEXITCODE
    $listText = $listLines -join [Environment]::NewLine
    if ($listExitCode -ne 0) {
        throw (
            'No se pudo enumerar las sondas ST-LINK. ' +
            "STM32CubeProgrammer terminó con código $listExitCode.`n" +
            $listText)
    }

    $blockPattern =
        '(?ms)^[ \t]*ST-Link Probe[ \t]+\d+[ \t]*:[ \t]*\r?\n' +
        '(?<block>.*?)' +
        '(?=^[ \t]*ST-Link Probe[ \t]+\d+[ \t]*:|' +
        '^[ \t]*-{5,}|\z)'
    foreach ($blockMatch in [regex]::Matches($listText, $blockPattern)) {
        $block = $blockMatch.Groups['block'].Value
        $serialMatch = [regex]::Match(
            $block,
            '(?m)^[ \t]*ST-LINK SN[ \t]*:[ \t]*(?<value>[^\r\n]+)')
        $boardMatch = [regex]::Match(
            $block,
            '(?m)^[ \t]*Board Name[ \t]*:[ \t]*(?<value>[^\r\n]+)')

        if ($serialMatch.Success) {
            [pscustomobject]@{
                Serial = $serialMatch.Groups['value'].Value.Trim()
                BoardName = if ($boardMatch.Success) {
                    $boardMatch.Groups['value'].Value.Trim()
                }
                else {
                    ''
                }
            }
        }
    }
}

$expectedBoardNames = @{
    f746 = 'NUCLEO-F746ZG'
    h755 = 'NUCLEO-H755ZI-Q'
}
$connectedProbes = @(
    Get-ConnectedStLinkProbes -Programmer $tools.Programmer)
$selectedProbes = @(
    $connectedProbes |
        Where-Object { $_.Serial -ieq $normalizedProbeSerial })

if ($selectedProbes.Count -ne 1) {
    $available = if ($connectedProbes.Count -eq 0) {
        'ninguna'
    }
    else {
        ($connectedProbes |
            ForEach-Object {
                if ($_.BoardName) {
                    "$($_.Serial) [$($_.BoardName)]"
                }
                else {
                    "$($_.Serial) [Board Name no informado]"
                }
            }) -join ', '
    }
    throw (
        "La sonda ST-LINK '$normalizedProbeSerial' no aparece exactamente " +
        "una vez. Sondas detectadas: $available.")
}

$selectedProbe = $selectedProbes[0]
$expectedBoardName = $expectedBoardNames[$Board]
if ($selectedProbe.BoardName -cne $expectedBoardName) {
    throw (
        "La sonda $normalizedProbeSerial corresponde a " +
        "'$($selectedProbe.BoardName)', pero -Board $Board exige " +
        "'$expectedBoardName'. No se programó ninguna memoria.")
}

$connection = @(
    'port=SWD',
    'mode=UR',
    'reset=HWrst',
    "sn=$normalizedProbeSerial")
Write-Host (
    "Sonda validada: $normalizedProbeSerial [$expectedBoardName].")

if ($ResetOnly) {
    if ($PSCmdlet.ShouldProcess(
            "$expectedBoardName / $normalizedProbeSerial",
            'Aplicar reset hardware sin programar Flash')) {
        & $tools.Programmer -c @connection -rst
        if ($LASTEXITCODE -ne 0) {
            throw "Falló el reset hardware de $expectedBoardName."
        }
    }
    exit 0
}

if ($Board -eq 'f746') {
    $suffix = if ($Representation -eq 'fixed') { '_fixed' } else { '' }
    $image = Join-Path $resolvedBuildDirectory (
        "f746_${System}_${Method}${suffix}.hex")
    if (-not (Test-Path -LiteralPath $image -PathType Leaf)) {
        throw "No existe $image. Ejecuta primero la tarea build:f746."
    }

    if ($PSCmdlet.ShouldProcess(
            "$expectedBoardName / $normalizedProbeSerial",
            "Programar y verificar $image")) {
        if ($NoReset) {
            & $tools.Programmer -c @connection -halt -w $image -v
        }
        else {
            & $tools.Programmer -c @connection -w $image -v -rst
        }
        if ($LASTEXITCODE -ne 0) {
            throw 'Falló la programación de la NUCLEO-F746ZG.'
        }
    }
    exit 0
}

$suffix = if ($Representation -eq 'fixed') { '_fixed' } else { '' }
$cm7Image = Join-Path $resolvedBuildDirectory (
    "h755_m7_${System}_${Method}${suffix}.hex")
$cm4Image = Join-Path $resolvedBuildDirectory 'h755_m4_uart.hex'

foreach ($image in @($cm7Image, $cm4Image)) {
    if (-not (Test-Path -LiteralPath $image -PathType Leaf)) {
        throw "No existe $image. Ejecuta primero la tarea build:h755."
    }
}

if ($PSCmdlet.ShouldProcess(
        "$expectedBoardName / $normalizedProbeSerial / Cortex-M7",
        "Programar y verificar $cm7Image")) {
    Write-Host 'Programando Cortex-M7 en Flash bank 1...'
    & $tools.Programmer -c @connection -halt -w $cm7Image -v
    if ($LASTEXITCODE -ne 0) {
        throw 'Falló la programación del Cortex-M7.'
    }
}

if ($PSCmdlet.ShouldProcess(
        "$expectedBoardName / $normalizedProbeSerial / Cortex-M4",
        $(if ($NoReset) {
            "Programar y verificar $cm4Image conservando el objetivo detenido"
        }
        else {
            "Programar, verificar y reiniciar $cm4Image"
        }))) {
    Write-Host 'Programando Cortex-M4 en Flash bank 2...'
    if ($NoReset) {
        & $tools.Programmer -c @connection -halt -w $cm4Image -v
    }
    else {
        & $tools.Programmer -c @connection -halt -w $cm4Image -v -rst
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Falló la programación del Cortex-M4.'
    }
}
