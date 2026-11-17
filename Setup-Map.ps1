[CmdletBinding()]
param([string]$Source, [string]$Sheet)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Sheet -and -not $Source) { throw 'Use -Source with -Sheet when importing a workbook.' }
# Check the complete Python version before fetching the large runtime bundle.
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install 64-bit Python 3.11.5 or newer within the 3.11 series, including the Python launcher.' }
    py -3.11 ./src/capa_map/bootstrap.py --check-python
    if ($LASTEXITCODE -ne 0) { throw 'A supported 64-bit Python 3.11 interpreter is required. See the details above.' }
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'The Python virtual environment could not be created.' }
}
& ./.venv/Scripts/python.exe ./src/capa_map/bootstrap.py --check-python
if ($LASTEXITCODE -ne 0) { throw 'The existing .venv uses an unsupported interpreter. Install a supported Python 3.11 build and recreate .venv; see README.md.' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Install Git and Git LFS before running setup.' }
git lfs version
if ($LASTEXITCODE -ne 0) { throw 'Install Git LFS, then rerun Setup-Map.ps1.' }
git lfs install --local
if ($LASTEXITCODE -ne 0) { throw 'Git LFS could not be configured for this checkout.' }
git lfs pull --include='data/sources/trackwise_records.csv,data/assets/**,data/models/huggingface/hub/models--BAAI--bge-large-en-v1.5/**' --exclude=''
if ($LASTEXITCODE -ne 0) { throw 'MAP runtime files could not be downloaded. Check GitHub access and Git LFS availability.' }
& ./.venv/Scripts/python.exe ./src/capa_map/bootstrap.py --root $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'MAP runtime verification failed. See the missing or changed files above.' }
& ./.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'MAP dependencies could not be installed.' }
& ./.venv/Scripts/python.exe -m pip install -e '.[test]' --no-deps --no-build-isolation
if ($LASTEXITCODE -ne 0) { throw 'MAP package installation failed.' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
# Prepare the bundled source on first setup; preserve an existing configured source/run.
if (-not $Source) {
    & ./.venv/Scripts/python.exe -m capa_map.bootstrap --root $PSScriptRoot --initialize-source
    if ($LASTEXITCODE -ne 0) { throw 'The bundled source could not be prepared.' }
}
if ($Source) { $env:PYTHONPATH = "$PSScriptRoot\src"; if ($Sheet) { & ./.venv/Scripts/python.exe -c 'from capa_map.cli import import_source; import_source()' $Source --sheet $Sheet } else { & ./.venv/Scripts/python.exe -c 'from capa_map.cli import import_source; import_source()' $Source }; if ($LASTEXITCODE -ne 0) { throw 'Source import failed.' } }
Write-Host 'MAP setup complete. Configure OPENAI_API_KEY in .env, then run .\Start-Map.ps1.'
