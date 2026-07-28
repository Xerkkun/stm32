[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (
    Join-Path $PSScriptRoot '..')).Path
$stm32Root = (Resolve-Path (
    Join-Path $projectRoot '..')).Path
$h7Root = Join-Path $projectRoot 'vendor\STM32CubeH7'
$f7Root = Join-Path $stm32Root 'Lorenz_efork'
$cubeH7Tag = 'v1.13.0'
$cubeH7Commit = '5abb9764b32e11a6557b90bf39531528019b5761'
$cmsisH7Commit = '8f922cdc7cc6de2344e75ddd657889f4ff761790'
$halH7Commit = 'a1996eed9172b59887bafaaa0ea1816ea14d48b5'

. (Join-Path $PSScriptRoot 'Resolve-Stm32Tools.ps1')
$tools = Resolve-Stm32Tools
Enable-Stm32ToolEnvironment $tools

$requiredF7 = @(
    'Drivers\CMSIS\Include\core_cm7.h',
    'Drivers\CMSIS\Device\ST\STM32F7xx\Include\stm32f746xx.h',
    'Drivers\STM32F7xx_HAL_Driver\Src\stm32f7xx_hal.c'
)
foreach ($relative in $requiredF7) {
    $candidate = Join-Path $f7Root $relative
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Falta la dependencia F7 existente: $candidate"
    }
}

if (-not (Test-Path -LiteralPath $h7Root -PathType Container)) {
    & git clone --depth 1 --branch $cubeH7Tag --filter=blob:none --sparse `
        https://github.com/STMicroelectronics/STM32CubeH7.git $h7Root
    if ($LASTEXITCODE -ne 0) {
        throw 'No se pudo descargar STM32CubeH7.'
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $h7Root '.git'))) {
    throw "Existe $h7Root, pero no es el checkout oficial esperado."
}

$cmsisRoot = Join-Path $h7Root 'Drivers\CMSIS\Device\ST\STM32H7xx'
$halRoot = Join-Path $h7Root 'Drivers\STM32H7xx_HAL_Driver'
$h7Safe = $h7Root.Replace('\', '/')
$cmsisSafe = $cmsisRoot.Replace('\', '/')
$halSafe = $halRoot.Replace('\', '/')

& git -c "safe.directory=$h7Safe" -C $h7Root `
    sparse-checkout set --skip-checks `
    Drivers/CMSIS/Include `
    Drivers/CMSIS/Device/ST/STM32H7xx `
    Drivers/STM32H7xx_HAL_Driver
if ($LASTEXITCODE -ne 0) {
    throw 'No se pudo configurar el checkout disperso de STM32CubeH7.'
}

& git -c "safe.directory=$h7Safe" -C $h7Root `
    submodule update --init --depth 1 `
    Drivers/CMSIS/Device/ST/STM32H7xx `
    Drivers/STM32H7xx_HAL_Driver
if ($LASTEXITCODE -ne 0) {
    throw 'No se pudieron descargar CMSIS/HAL para STM32H7.'
}

$cmsisCore = Join-Path $h7Root 'Drivers\CMSIS\Include\core_cm7.h'
if (-not (Test-Path -LiteralPath $cmsisCore -PathType Leaf)) {
    throw "Falta CMSIS-Core en el paquete H7 fijado: $cmsisCore"
}

$actualH7 = (& git -c "safe.directory=$h7Safe" `
    -C $h7Root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'No se pudo leer la revisión de STM32CubeH7.'
}
$actualCmsis = (& git -c "safe.directory=$cmsisSafe" `
    -C $cmsisRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'No se pudo leer la revisión de CMSIS H7.'
}
$actualHal = (& git -c "safe.directory=$halSafe" `
    -C $halRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'No se pudo leer la revisión de HAL H7.'
}

if ($actualH7 -ne $cubeH7Commit) {
    throw "STM32CubeH7 no coincide: $actualH7 (esperado $cubeH7Commit)."
}
if ($actualCmsis -ne $cmsisH7Commit) {
    throw "CMSIS H7 no coincide: $actualCmsis (esperado $cmsisH7Commit)."
}
if ($actualHal -ne $halH7Commit) {
    throw "HAL H7 no coincide: $actualHal (esperado $halH7Commit)."
}

Write-Host 'Dependencias verificadas:'
Write-Host "  STM32CubeH7 $cubeH7Tag  $actualH7"
Write-Host "  CMSIS H7              $actualCmsis"
Write-Host "  HAL H7                $actualHal"
Write-Host "  GCC                    $($tools.Gcc)"
Write-Host "  Ninja                  $($tools.Ninja)"
Write-Host "  Programmer             $($tools.Programmer)"
