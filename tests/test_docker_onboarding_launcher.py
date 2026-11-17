"""Run the real PowerShell entry point against a controlled Docker CLI boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario', ['full', 'prepare', 'failed_build', 'unrelated'])
def test_complete_docker_launcher_boundaries(tmp_path, scenario):
    shell = shutil.which('powershell') or shutil.which('pwsh')
    if not shell:
        pytest.skip('PowerShell launcher requires PowerShell')
    name = 'capa-ai-map-unit-' + uuid4().hex[:12]
    data = tmp_path / 'persistent data'
    (data / '.setup').mkdir(parents=True)
    (data / '.setup/source.json').write_text(json.dumps({
        'CAPA_SOURCE_SNAPSHOT': '/data/sources/fixture.sqlite3', 'CAPA_SOURCE_CSV': '/data/sources/fixture.csv'}))
    configuration = tmp_path / 'docker settings.env'
    configuration.write_text('OPENAI_API_KEY=fictional-key\nOPENAI_BASE_URL=https://fixture.invalid/v1\n')
    calls = tmp_path / 'calls.jsonl'
    harness = tmp_path / 'harness.ps1'
    harness.write_text(r'''
function global:docker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArguments)
    @{args=@($CommandArguments)} | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:TEST_CALLS
    $global:LASTEXITCODE=0
    if ($CommandArguments[0] -eq 'info') { 'linux'; return }
    if ($CommandArguments[0] -eq 'container' -and $CommandArguments[1] -eq 'ls') {
        if ($env:TEST_SCENARIO -eq 'unrelated') { 'fictional-container' }; return
    }
    if ($CommandArguments[0] -eq 'container' -and $CommandArguments[1] -eq 'inspect') {
        '[{"Config":{"Labels":{"capa.map.project":"another-checkout"}},"State":{"Running":true}}]'; return
    }
    if ($CommandArguments[0] -eq 'image') { 'sha256:fixture'; return }
    if ($CommandArguments[0] -eq 'network') { 'capa-ai'; return }
    if ($CommandArguments[0] -eq 'build') {
        Write-Error 'Fictional Docker progress written to stderr'
        if ($env:TEST_SCENARIO -eq 'failed_build') { $global:LASTEXITCODE=11 }
    }
}
$argsMap=@{ContainerName=$env:TEST_CONTAINER; EnvFile=$env:TEST_CONFIG; DataDirectory=$env:TEST_DATA; Port=28101; Limit=0; NonInteractive=$true}
if ($env:TEST_SCENARIO -eq 'prepare') { $argsMap.PrepareOnly=$true }
& $env:TEST_LAUNCHER @argsMap
''', encoding='utf8')
    env = dict(os.environ, TEST_CALLS=str(calls), TEST_CONFIG=str(configuration), TEST_DATA=str(data),
               TEST_LAUNCHER=str(ROOT / 'Run-Map.ps1'), TEST_CONTAINER=name, TEST_SCENARIO=scenario)
    try:
        result = subprocess.run([shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
                                env=env, capture_output=True, text=True, timeout=60)
        recorded = [json.loads(line)['args'] for line in calls.read_text(encoding='utf-8-sig').splitlines()]
        if scenario in ('failed_build', 'unrelated'):
            assert result.returncode != 0
            assert not any(row[0] in ('run', 'stop', 'exec') for row in recorded)
            return
        assert result.returncode == 0, result.stdout + result.stderr
        prepare = next(row for row in recorded if row[0] == 'run' and 'prepare' in row)
        assert 'capa_map.docker_setup' in prepare
        assert f'type=bind,source={data},target=/data' in prepare
        assert any('target=/bundle,readonly' in item for item in prepare)
        services = [row for row in recorded if row[0] == 'run' and '--detach' in row]
        if scenario == 'prepare':
            assert not services
            assert not any('exec' == row[0] for row in recorded)
        else:
            assert services[0][services[0].index('--publish')+1] == '127.0.0.1:28101:8101'
            monitor = next(row for row in recorded if row[0] == 'exec' and 'run' in row)
            assert monitor[monitor.index('--limit')+1] == '0'
    finally:
        shutil.rmtree(ROOT / '.local' / name, ignore_errors=True)
