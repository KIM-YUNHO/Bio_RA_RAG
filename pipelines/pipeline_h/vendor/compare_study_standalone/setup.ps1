param(
    [string]$Python = 'python',
    [ValidateSet('all', 'paddle', 'cascade', 'docling')][string]$Case = 'all',
    [string]$TorchIndex = 'https://download.pytorch.org/whl/cu126'
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Exe (exit $LASTEXITCODE)" }
}
Invoke-Checked -Exe $Python -Arguments @('-c', 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 required"')
$environments = @(@{ Name = '.venv'; Requirements = 'requirements-controller.txt'; GPU = $false })
if ($Case -in @('all', 'paddle', 'cascade')) {
    $environments += @{ Name = '.venv-paddle'; Requirements = 'requirements-paddle.txt'; GPU = $true }
}
if ($Case -in @('all', 'docling')) {
    $environments += @{ Name = '.venv-docling'; Requirements = 'requirements-docling.txt'; GPU = $true }
}
foreach ($environment in $environments) {
    $venvPath = Join-Path $taskRoot $environment.Name
    $venvPython = Join-Path $venvPath 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Invoke-Checked -Exe $Python -Arguments @('-m', 'venv', $venvPath)
    }
    Invoke-Checked -Exe $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
    if ($environment.GPU) {
        Invoke-Checked -Exe $venvPython -Arguments @('-m', 'pip', 'install', 'torch==2.14.1', 'torchvision==0.29.1', '--index-url', $TorchIndex)
    }
    Invoke-Checked -Exe $venvPython -Arguments @('-m', 'pip', 'install', '-r', (Join-Path $taskRoot $environment.Requirements))
    if ($environment.Name -eq '.venv-docling') {
        # Both wheels write the same onnxruntime module. Keep the GPU wheel only.
        Invoke-Checked -Exe $venvPython -Arguments @('-m', 'pip', 'uninstall', '-y', 'onnxruntime')
        Invoke-Checked -Exe $venvPython -Arguments @('-m', 'pip', 'install', '--force-reinstall', '--no-deps', 'onnxruntime-gpu==1.23.2')
    }
}
$configPath = Join-Path $taskRoot 'config.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath (Join-Path $taskRoot 'config.example.json') -Destination $configPath
}
Write-Host 'Environments created. Download or configure local models, then run: .venv\Scripts\python.exe run.py check'
