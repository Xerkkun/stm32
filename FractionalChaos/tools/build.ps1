[CmdletBinding()]
param(
    [ValidateSet('host', 'f746', 'h755', 'all')]
    [string]$Board = 'all',

    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',

    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (
    Join-Path $PSScriptRoot '..')).Path

. (Join-Path $PSScriptRoot 'Resolve-Stm32Tools.ps1')
$tools = Resolve-Stm32Tools
Enable-Stm32ToolEnvironment $tools

& (Join-Path $PSScriptRoot 'bootstrap.ps1')
if ($LASTEXITCODE -ne 0) {
    throw 'Falló la verificación de dependencias.'
}

function Invoke-ProjectBuild {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PresetName,

        [Parameter(Mandatory = $true)]
        [string]$BuildDirectory
    )

    $resolvedBuild = [IO.Path]::GetFullPath(
        (Join-Path $projectRoot $BuildDirectory))
    $allowedRoot = [IO.Path]::GetFullPath(
        (Join-Path $projectRoot 'build'))

    if (-not $resolvedBuild.StartsWith(
        $allowedRoot,
        [StringComparison]::OrdinalIgnoreCase)) {
        throw "Directorio de build fuera del proyecto: $resolvedBuild"
    }

    if ($Clean -and (Test-Path -LiteralPath $resolvedBuild)) {
        Remove-Item -LiteralPath $resolvedBuild -Recurse -Force
    }

    & $tools.CMake --preset $PresetName
    if ($LASTEXITCODE -ne 0) {
        throw "Falló la configuración $PresetName."
    }

    & $tools.CMake --build --preset "$PresetName-build"
    if ($LASTEXITCODE -ne 0) {
        throw "Falló la compilación $PresetName."
    }
}

$configurationSuffix = $Configuration.ToLowerInvariant()
switch ($Board) {
    'host' {
        Invoke-ProjectBuild "host-$configurationSuffix" `
            "build\host-$configurationSuffix"
    }
    'f746' {
        Invoke-ProjectBuild "f746-$configurationSuffix" `
            "build\f746-$configurationSuffix"
    }
    'h755' {
        Invoke-ProjectBuild "h755-$configurationSuffix" `
            "build\h755-$configurationSuffix"
    }
    'all' {
        Invoke-ProjectBuild "host-$configurationSuffix" `
            "build\host-$configurationSuffix"
        Invoke-ProjectBuild "f746-$configurationSuffix" `
            "build\f746-$configurationSuffix"
        Invoke-ProjectBuild "h755-$configurationSuffix" `
            "build\h755-$configurationSuffix"
    }
}
