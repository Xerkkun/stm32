<#
.SYNOPSIS
Compila una celda física en un directorio separado por placa y decimación.

.DESCRIPTION
El directorio queda fijado a build/campaign/<board>/decim-<N>-release. Esto
evita reutilizar accidentalmente un CMakeCache creado para otra placa o para
otra decimación. El target se valida contra la nomenclatura del repositorio.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('f746', 'h755')]
    [string]$Board,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 1048576)]
    [int]$Decimation,

    [Parameter(Mandatory = $true)]
    [string]$Target,

    [switch]$BenchmarkMode,

    [switch]$BufferedCaptureMode,

    [ValidateRange(1, 12000)]
    [int]$BufferedCaptureSamples = 12000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (
    Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'Resolve-Stm32Tools.ps1')
$tools = Resolve-Stm32Tools
Enable-Stm32ToolEnvironment $tools

$targetPattern = if ($Board -eq 'f746') {
    '^f746_(lorenz|rossler|chen)_(efork3|gl|m2sfrk)(_fixed)?$'
}
else {
    '^h755_m7_(lorenz|rossler|chen)_(efork3|gl|m2sfrk)(_fixed)?$'
}
if ($Target -cnotmatch $targetPattern) {
    throw "Target '$Target' no corresponde a Board '$Board'."
}

if ($BenchmarkMode -and $BufferedCaptureMode) {
    throw 'BenchmarkMode y BufferedCaptureMode son mutuamente excluyentes.'
}
if ($BufferedCaptureMode -and $Decimation -ne 1) {
    throw 'BufferedCaptureMode conserva cada paso y requiere Decimation=1.'
}

$profileDirectory = if ($BenchmarkMode) {
    'benchmark-10000-release'
}
elseif ($BufferedCaptureMode) {
    "dense-$BufferedCaptureSamples-decim-1-release"
}
else {
    "decim-$Decimation-release"
}
$buildDirectory = [IO.Path]::GetFullPath((
    Join-Path $projectRoot (
        "build\campaign\$Board\$profileDirectory")))
$allowedRoot = [IO.Path]::GetFullPath((
    Join-Path $projectRoot "build\campaign\$Board"))
$allowedPrefix = $allowedRoot.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar
if (-not $buildDirectory.StartsWith(
        $allowedPrefix,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "Directorio de campaña fuera de $allowedRoot."
}

$toolchain = Join-Path $projectRoot 'cmake\arm-none-eabi.cmake'
$decimationVariable = if ($Board -eq 'f746') {
    'FC_F746_OUTPUT_DECIMATION'
}
else {
    'FC_H755_OUTPUT_DECIMATION'
}
$benchmarkVariable = if ($Board -eq 'f746') {
    'FC_F746_BENCHMARK_MODE'
}
else {
    'FC_H755_BENCHMARK_MODE'
}
$benchmarkValue = if ($BenchmarkMode) { 'ON' } else { 'OFF' }
$bufferedCaptureVariable = if ($Board -eq 'f746') {
    'FC_F746_BUFFERED_CAPTURE_MODE'
}
else {
    'FC_H755_BUFFERED_CAPTURE_MODE'
}
$bufferedCaptureSamplesVariable = if ($Board -eq 'f746') {
    'FC_F746_BUFFERED_CAPTURE_SAMPLES'
}
else {
    'FC_H755_BUFFERED_CAPTURE_SAMPLES'
}
$bufferedCaptureValue = if ($BufferedCaptureMode) { 'ON' } else { 'OFF' }

if (-not $PSCmdlet.ShouldProcess(
        $buildDirectory,
        "Configurar y compilar $Target (benchmark=$benchmarkValue, " +
        "captura_RAM=$bufferedCaptureValue, " +
        "decimación=$Decimation)")) {
    return
}

& (Join-Path $PSScriptRoot 'bootstrap.ps1')
if ($LASTEXITCODE -ne 0) {
    throw 'Falló la verificación de dependencias.'
}

$configureArguments = @(
    '-S', $projectRoot,
    '-B', $buildDirectory,
    '-G', 'Ninja',
    '-DCMAKE_BUILD_TYPE=Release',
    "-DFC_PLATFORM=$Board",
    "-D${decimationVariable}=$Decimation",
    "-D${benchmarkVariable}=$benchmarkValue",
    "-D${bufferedCaptureVariable}=$bufferedCaptureValue",
    "-D${bufferedCaptureSamplesVariable}=$BufferedCaptureSamples"
)
if (-not (Test-Path -LiteralPath (
        Join-Path $buildDirectory 'CMakeCache.txt') -PathType Leaf)) {
    $configureArguments += "-DCMAKE_TOOLCHAIN_FILE=$toolchain"
}
& $tools.CMake @configureArguments
if ($LASTEXITCODE -ne 0) {
    throw "Falló la configuración de $Target."
}

$targets = @($Target)
if ($Board -eq 'h755') {
    $targets += 'h755_m4_uart'
}
& $tools.CMake --build $buildDirectory --target @targets
if ($LASTEXITCODE -ne 0) {
    throw "Falló la compilación de $($targets -join ', ')."
}

Write-Host (
    "Build de campaña listo: $buildDirectory [$($targets -join ', ')].")
