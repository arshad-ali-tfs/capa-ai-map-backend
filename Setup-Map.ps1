param([string]$Source, [string]$Sheet)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { py -3.11 -m venv .venv; if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' } }
& ./.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'MAP dependencies could not be installed.' }
& ./.venv/Scripts/python.exe -m pip install -e '.[test]' --no-deps --no-build-isolation
if ($LASTEXITCODE -ne 0) { throw 'MAP package installation failed.' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
if ($Source) { $env:PYTHONPATH = "$PSScriptRoot\src"; if ($Sheet) { & ./.venv/Scripts/python.exe -c 'from capa_map.cli import import_source; import_source()' $Source --sheet $Sheet } else { & ./.venv/Scripts/python.exe -c 'from capa_map.cli import import_source; import_source()' $Source }; if ($LASTEXITCODE -ne 0) { throw 'Source import failed.' } }
