<#
.SYNOPSIS
Compila una celda física en un directorio separado por placa y decimación.

.DESCRIPTION
El directorio queda bajo build/campaign por defecto y puede seleccionarse con
BuildRoot siempre que permanezca dentro de build/. Esto evita reutilizar
accidentalmente un CMakeCache creado para otra placa o decimación.
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

    [string]$BuildRoot = 'build/campaign',

    [switch]$BenchmarkMode,

    [switch]$PrimaryHandshake,

    [ValidateRange(1, 512)]
    [int]$EnergyWorkMultiplier = 1,

    [switch]$BufferedCaptureMode,

    [ValidateRange(1, 12000)]
    [int]$BufferedCaptureSamples = 12000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (
    Join-Path $PSScriptRoot '..')).Path

$targetPattern = if ($Board -eq 'f746') {
    '^f746_(lorenz|rossler|chen|liu|hammouch_mekkaoui)_(efork3|gl|m2sfrk)(_fixed)?$'
}
else {
    '^h755_m7_(lorenz|rossler|chen|liu|hammouch_mekkaoui)_(efork3|gl|m2sfrk)(_fixed)?$'
}
if ($Target -cnotmatch $targetPattern) {
    throw "Target '$Target' no corresponde a Board '$Board'."
}

if ($BenchmarkMode -and $BufferedCaptureMode) {
    throw 'BenchmarkMode y BufferedCaptureMode son mutuamente excluyentes.'
}
if ($PrimaryHandshake -and -not $BenchmarkMode) {
    throw 'PrimaryHandshake requiere BenchmarkMode.'
}
if ($EnergyWorkMultiplier -ne 1 -and -not $PrimaryHandshake) {
    throw 'EnergyWorkMultiplier distinto de 1 requiere PrimaryHandshake.'
}
if ($BufferedCaptureMode -and $Decimation -ne 1) {
    throw 'BufferedCaptureMode conserva cada paso y requiere Decimation=1.'
}

$profileDirectory = if ($PrimaryHandshake) {
    'benchmark-10000-primary-handshake-release'
}
elseif ($BenchmarkMode) {
    'benchmark-10000-release'
}
elseif ($BufferedCaptureMode) {
    "dense-$BufferedCaptureSamples-decim-1-release"
}
else {
    "decim-$Decimation-release"
}
$allowedBuildRoot = [IO.Path]::GetFullPath((
    Join-Path $projectRoot 'build'))
$allowedBuildPrefix = $allowedBuildRoot.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar
$resolvedBuildRoot = if ([IO.Path]::IsPathRooted($BuildRoot)) {
    [IO.Path]::GetFullPath($BuildRoot)
}
else {
    [IO.Path]::GetFullPath((Join-Path $projectRoot $BuildRoot))
}
if (
    $resolvedBuildRoot -ine $allowedBuildRoot -and
    -not $resolvedBuildRoot.StartsWith(
        $allowedBuildPrefix,
        [StringComparison]::OrdinalIgnoreCase)
) {
    throw "BuildRoot debe permanecer dentro de $allowedBuildRoot."
}

$buildDirectory = [IO.Path]::GetFullPath((
    Join-Path $resolvedBuildRoot (
        "$Board\$profileDirectory")))
$buildRootPrefix = $resolvedBuildRoot.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar
if (-not $buildDirectory.StartsWith(
        $buildRootPrefix,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "Directorio de campaña fuera de $resolvedBuildRoot."
}

. (Join-Path $PSScriptRoot 'Resolve-Stm32Tools.ps1')
$tools = Resolve-Stm32Tools
Enable-Stm32ToolEnvironment $tools

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
$energyMarkerVariable = if ($Board -eq 'f746') {
    'FC_F746_ENERGY_MARKER'
}
else {
    'FC_H755_ENERGY_MARKER'
}
$energyMarkerValue = $benchmarkValue
$energyWorkMultiplierVariable = if ($Board -eq 'f746') {
    'FC_F746_ENERGY_WORK_MULTIPLIER'
}
else {
    'FC_H755_ENERGY_WORK_MULTIPLIER'
}
$primaryHandshakeVariable = if ($Board -eq 'f746') {
    'FC_F746_PRIMARY_HANDSHAKE'
}
else {
    'FC_H755_PRIMARY_HANDSHAKE'
}
$primaryHandshakeValue = if ($PrimaryHandshake) { 'ON' } else { 'OFF' }
$clockReferenceVariable = if ($Board -eq 'f746') {
    'FC_F746_CLOCK_REFERENCE'
}
else {
    'FC_H755_CLOCK_REFERENCE'
}
$clockReferenceValue = $primaryHandshakeValue
$runtimeProbeVariable = if ($Board -eq 'f746') {
    'FC_F746_RUNTIME_PROBE'
}
else {
    'FC_H755_RUNTIME_PROBE'
}
$runtimeProbeValue = $primaryHandshakeValue
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
        "handshake_primario=$primaryHandshakeValue, " +
        "marcador_energía=$energyMarkerValue, " +
        "multiplicador_energía=$EnergyWorkMultiplier, " +
        "referencia_reloj=$clockReferenceValue, " +
        "watermark_runtime=$runtimeProbeValue, " +
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
    "-D${energyMarkerVariable}=$energyMarkerValue",
    "-D${energyWorkMultiplierVariable}=$EnergyWorkMultiplier",
    "-D${clockReferenceVariable}=$clockReferenceValue",
    "-D${runtimeProbeVariable}=$runtimeProbeValue",
    "-D${primaryHandshakeVariable}=$primaryHandshakeValue",
    "-D${bufferedCaptureVariable}=$bufferedCaptureValue",
    "-D${bufferedCaptureSamplesVariable}=$BufferedCaptureSamples"
)
if ($Board -eq 'h755') {
    # The physical campaign is frozen at the manifest's 400 MHz profile.
    # Pass every cacheable safety switch on every configure so a build tree
    # previously used for the optional 480 MHz/LDO or smoke profile cannot
    # leak those settings into a reportable campaign image.
    $configureArguments += @(
        '-DFC_H755_CLOCK_480=OFF',
        '-DFC_H755_LDO_MODIFICATION_CONFIRMED=OFF',
        '-DFC_H755_SMOKE_DIAGNOSTICS=OFF'
    )
}
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
