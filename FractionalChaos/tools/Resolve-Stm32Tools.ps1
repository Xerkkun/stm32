Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-LatestVersionDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return $null
    }

    return Get-ChildItem -LiteralPath $Root -Directory |
        Sort-Object -Property @{
            Expression = {
                $numeric = $_.Name -replace '\+.*$', ''
                try {
                    [version]$numeric
                }
                catch {
                    [version]'0.0'
                }
            }
        } -Descending |
        Select-Object -First 1
}

function Find-FirstExecutable {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Candidates
    )

    foreach ($candidate in $Candidates) {
        if ($candidate -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

function Resolve-Stm32Tools {
    $bundleRoot = Join-Path $env:LOCALAPPDATA 'STM32Cube\bundles'

    $gccBundle = Get-LatestVersionDirectory (
        Join-Path $bundleRoot 'gnu-tools-for-stm32')
    $ninjaBundle = Get-LatestVersionDirectory (
        Join-Path $bundleRoot 'ninja')
    $programmerBundle = Get-LatestVersionDirectory (
        Join-Path $bundleRoot 'programmer')
    $cmakeBundle = Get-LatestVersionDirectory (
        Join-Path $bundleRoot 'cmake')

    $idePluginRoot = 'C:\ST\STM32CubeIDE_2.1.0\STM32CubeIDE\plugins'
    $ideGcc = Get-ChildItem -LiteralPath $idePluginRoot -Directory `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -like
            'com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32*'
        } |
        Sort-Object Name -Descending |
        Select-Object -First 1

    $ideNinja = Get-ChildItem -LiteralPath $idePluginRoot -Directory `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -like
            'com.st.stm32cube.ide.mcu.externaltools.ninja*'
        } |
        Sort-Object Name -Descending |
        Select-Object -First 1

    $ideProgrammer = Get-ChildItem -LiteralPath $idePluginRoot -Directory `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -like
            'com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer*'
        } |
        Sort-Object Name -Descending |
        Select-Object -First 1

    $gcc = Find-FirstExecutable @(
        $(if ($gccBundle) {
            Join-Path $gccBundle.FullName 'bin\arm-none-eabi-gcc.exe'
        }),
        $(if ($ideGcc) {
            Join-Path $ideGcc.FullName 'tools\bin\arm-none-eabi-gcc.exe'
        })
    )
    $ninja = Find-FirstExecutable @(
        $(if ($ninjaBundle) {
            Join-Path $ninjaBundle.FullName 'bin\ninja.exe'
        }),
        $(if ($ideNinja) {
            Join-Path $ideNinja.FullName 'tools\bin\ninja.exe'
        })
    )
    $programmer = Find-FirstExecutable @(
        $(if ($programmerBundle) {
            Join-Path $programmerBundle.FullName 'bin\STM32_Programmer_CLI.exe'
        }),
        $(if ($ideProgrammer) {
            Join-Path $ideProgrammer.FullName `
                'tools\bin\STM32_Programmer_CLI.exe'
        })
    )

    $cmakeCommand = Get-Command cmake.exe -ErrorAction SilentlyContinue
    $cmake = Find-FirstExecutable @(
        $(if ($cmakeCommand) { $cmakeCommand.Source }),
        $(if ($cmakeBundle) {
            Join-Path $cmakeBundle.FullName 'bin\cmake.exe'
        })
    )

    if (-not $gcc) {
        throw 'No se encontró arm-none-eabi-gcc en STM32Cube ni CubeIDE.'
    }
    if (-not $ninja) {
        throw 'No se encontró Ninja en STM32Cube ni CubeIDE.'
    }
    if (-not $programmer) {
        throw 'No se encontró STM32_Programmer_CLI.'
    }
    if (-not $cmake) {
        throw 'No se encontró CMake.'
    }

    [pscustomobject]@{
        Gcc = $gcc
        GccBin = Split-Path -Parent $gcc
        Ninja = $ninja
        NinjaBin = Split-Path -Parent $ninja
        Programmer = $programmer
        ProgrammerBin = Split-Path -Parent $programmer
        CMake = $cmake
        CMakeBin = Split-Path -Parent $cmake
    }
}

function Enable-Stm32ToolEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Tools
    )

    $paths = @(
        $Tools.GccBin,
        $Tools.NinjaBin,
        $Tools.ProgrammerBin,
        $Tools.CMakeBin
    ) | Select-Object -Unique

    $env:Path = ($paths + $env:Path) -join [IO.Path]::PathSeparator
}
