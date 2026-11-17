[CmdletBinding()]
param(
    [ValidateRange(0,100000)][int]$Limit=0,
    [ValidateRange(1,65535)][int]$Port=8101,
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')][string]$ContainerName='capa-ai-map',
    [string]$Image='capa-ai-map:local', [string]$DataDirectory='data', [string]$EnvFile='.env.docker',
    [string]$Source, [string]$Sheet,
    [switch]$PrepareOnly, [switch]$ServeOnly, [switch]$NewRun, [switch]$Stop, [switch]$NonInteractive
)
$ErrorActionPreference='Stop'
if (@(@($PrepareOnly,$ServeOnly,$Stop) | Where-Object { $_ }).Count -gt 1) { throw 'Choose one of -PrepareOnly, -ServeOnly or -Stop.' }
if ($Sheet -and -not $Source) { throw 'Use -Source with -Sheet.' }
if ($NewRun -and ($PrepareOnly -or $ServeOnly -or $Stop)) { throw '-NewRun requires an analysis run.' }
$repoRoot=$PSScriptRoot
if ($ContainerName -ne 'capa-ai-map' -and -not $PSBoundParameters.ContainsKey('DataDirectory')) { $DataDirectory=".local/$ContainerName/data" }
function ProjectPath([string]$Value) { if (-not [IO.Path]::IsPathRooted($Value)) { $Value=Join-Path $repoRoot $Value }; return [IO.Path]::GetFullPath($Value) }
function HashText([string]$Value) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}
$projectId=(HashText $repoRoot.ToLowerInvariant()).Substring(0,12)
$localDirectory=Join-Path $repoRoot ".local\$ContainerName"
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
$launcherLog=Join-Path $localDirectory 'setup.log'
$originalPath=$env:PATH
$lock=$null
try {
    try { $lock=[IO.File]::Open((Join-Path $localDirectory 'launch.lock'),'OpenOrCreate','ReadWrite','None') } catch { throw 'Another setup/monitor for this container is active. Close that monitor before rerunning; the analysis keeps running.' }
    $dockerExe=Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerExe) { $dockerExe=@((Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),(Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1 }
    if (-not $dockerExe) { throw 'Install Docker Desktop and start its Linux engine. Host Python is not needed.' }
    function Invoke-Docker([string[]]$Arguments,[switch]$Capture,[switch]$AllowFailure) {
        $saved=$ErrorActionPreference; $lines=[Collections.Generic.List[string]]::new()
        try {
            $ErrorActionPreference='Continue'
            & $dockerExe @Arguments 2>&1 | ForEach-Object { $line=$_.ToString(); if (-not $line -or $line -eq 'System.Management.Automation.RemoteException') { return }; [IO.File]::AppendAllText($launcherLog,$line+[Environment]::NewLine); if ($Capture) { $lines.Add($line) } else { Write-Host $line } }
            $script:dockerExitCode=$LASTEXITCODE
        } finally { $ErrorActionPreference=$saved }
        if ($dockerExitCode -ne 0 -and -not $AllowFailure) { throw "Docker failed (exit $dockerExitCode). See the error above and $launcherLog" }
        if ($Capture) { return ($lines -join [Environment]::NewLine) }
    }
    Write-Host '[1/5] Checking Docker Linux engine...'
    $engine=Invoke-Docker @('info','--format','{{.OSType}}') -Capture
    if ($engine.Trim() -ne 'linux') { throw 'Start Docker Desktop with Linux containers and rerun.' }
    $existing=$null
    $containerId=Invoke-Docker @('container','ls','--all','--filter',('name=^/'+$ContainerName+'$'),'--format','{{.ID}}') -Capture
    if ($containerId) {
        $existing=@((Invoke-Docker @('container','inspect','--format','{"Image":{{json .Image}},"Config":{"Labels":{{json .Config.Labels}}},"State":{{json .State}}}',$ContainerName) -Capture) | ConvertFrom-Json)[0]
        if ($existing.Config.Labels.'capa.map.project' -ne $projectId) { throw "Container '$ContainerName' belongs to another launcher/checkout. Use -ContainerName capa-ai-map-demo -Port 18101, or stop/remove the old container yourself. No service was changed." }
    }
    if ($Stop) {
        if ($existing -and $existing.State.Running) { Invoke-Docker @('exec',$ContainerName,'python','-m','capa_map.docker_setup','active-check'); Invoke-Docker @('stop','-t','30',$ContainerName) }
        Write-Host 'MAP stopped or already stopped. Mounted data and saved work were retained.'; return
    }
    if ($existing -and $existing.State.Running) {
        Invoke-Docker @('exec',$ContainerName,'python','-m','capa_map.docker_setup','active-check') -Capture -AllowFailure | Out-Null
        if ($dockerExitCode -ne 0) {
            if ($Source) {
                $priorSource=Get-Content -LiteralPath (Join-Path (ProjectPath $DataDirectory) '.setup/source.json') -Raw | ConvertFrom-Json
                $inputHash=(Get-FileHash -LiteralPath (ProjectPath $Source) -Algorithm SHA256).Hash
                if ($inputHash -ne $priorSource.source_sha256 -or [string]$Sheet -ne [string]$priorSource.sheet) { throw 'A different source is being analysed. Wait for it to finish before changing the input.' }
            }
            if ($PrepareOnly -or $ServeOnly -or $NewRun) { throw 'An analysis is active. Resume its original command or wait before changing configuration.' }
            Invoke-Docker @('exec',$ContainerName,'python','-u','-m','capa_map.docker_setup','run','--limit',[string]$Limit); Write-Host "Backend ready: http://127.0.0.1:$Port/docs"; return
        }
    }
    $dataPath=ProjectPath $DataDirectory; $envPath=ProjectPath $EnvFile
    if ($dataPath.Contains(',') -or $repoRoot.Contains(',') -or ($Source -and $Source.Contains(','))) { throw 'Docker bind-mount paths cannot contain commas.' }
    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
    if (-not (Test-Path -LiteralPath $envPath)) { Copy-Item -LiteralPath (Join-Path $repoRoot 'docker.env.example') -Destination $envPath }
    function Read-Settings([string]$Path) {
        $values=@{}
        if (Test-Path -LiteralPath $Path) { foreach ($line in [IO.File]::ReadAllLines($Path)) {
            if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
                $name=$Matches[1]; $value=$Matches[2].Trim()
                if ($value.Length -ge 2 -and (($value.StartsWith("'") -and $value.EndsWith("'")) -or ($value.StartsWith('"') -and $value.EndsWith('"')))) { $value=$value.Substring(1,$value.Length-2) }
                $values[$name]=$value
            }
        } }; return $values
    }
    $configuration=Read-Settings $envPath; $native=Read-Settings (Join-Path $repoRoot '.env')
    foreach ($name in @('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_MODEL','OPENAI_REASONING_EFFORT','AZURE_OPENAI_ENDPOINT','AZURE_OPENAI_API_VERSION')) {
        $value=[Environment]::GetEnvironmentVariable($name,'Process')
        if ($value) { $configuration[$name]=$value } elseif (-not $configuration[$name] -and $native[$name]) { $configuration[$name]=$native[$name] }
    }
    if (-not $configuration['OPENAI_MODEL']) { $configuration['OPENAI_MODEL']='gpt-6.1-sol' }
    if (-not $configuration['OPENAI_REASONING_EFFORT']) { $configuration['OPENAI_REASONING_EFFORT']='medium' }
    function Require-Setting([string]$Name,[switch]$Secret) {
        if ($configuration[$Name] -and $configuration[$Name] -notmatch 'REPLACE_WITH_') { return }
        if ($NonInteractive) { throw "Set $Name in $envPath and rerun. No paid pipeline was submitted." }
        if ($Secret) {
            $secure=Read-Host "$Name (saved only in the ignored local Docker env file)" -AsSecureString
            $pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try { $value=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        } else { $value=Read-Host $Name }
        if (-not $value) { throw "$Name is required. No paid pipeline was submitted." }
        $configuration[$Name]=$value
        [IO.File]::AppendAllText($envPath,[Environment]::NewLine+"$Name=$value"+[Environment]::NewLine)
    }
    if (-not $PrepareOnly) {
        Require-Setting 'OPENAI_API_KEY' -Secret
        if (-not $configuration['OPENAI_BASE_URL'] -and -not $configuration['AZURE_OPENAI_ENDPOINT']) { Require-Setting 'OPENAI_BASE_URL' }
        if ($configuration['AZURE_OPENAI_ENDPOINT']) { Require-Setting 'AZURE_OPENAI_API_VERSION' }
    }
    $fixed=@{CAPA_ENV='local'; CAPA_DATA_DIR='/data'; CAPA_PROJECT_ROOT='/app'; MAP_HOST='0.0.0.0'; MAP_PORT='8101'; MAP_ASSETS_DIR='/data/assets'; HF_HOME='/data/models/huggingface'; HF_HUB_OFFLINE='1'; TRANSFORMERS_OFFLINE='1'; MAP_DASHBOARD_RUN_ID=''}
    foreach ($key in $fixed.Keys) { $configuration[$key]=$fixed[$key] }
    $runtimeEnv=Join-Path $localDirectory 'runtime.env'
    function Write-RuntimeEnv {
        $lines=@($configuration.Keys | Sort-Object | ForEach-Object { $value=[string]$configuration[$_]; if ($value.Contains("`n") -or $value.Contains("`r") -or $value.Contains([string][char]0)) { throw "Invalid multiline Docker value for $_" }; "$_=$value" })
        [IO.File]::WriteAllLines($runtimeEnv,[string[]]$lines,[Text.UTF8Encoding]::new($false))
    }
    Write-RuntimeEnv
    Write-Host '[2/5] Checking repository source/model downloads...'
    $manifest=Get-Content -LiteralPath (Join-Path $repoRoot 'runtime-assets.json') -Raw | ConvertFrom-Json
    $missing=$false
    foreach ($item in $manifest.files) {
        $path=[IO.Path]::GetFullPath((Join-Path $repoRoot ([string]$item.path)))
        if (-not $path.StartsWith($repoRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime manifest path escapes the checkout.' }
        $longPath=if ($path.StartsWith('\\')) { '\\?\UNC\'+$path.Substring(2) } else { '\\?\'+$path }
        if (-not [IO.File]::Exists($longPath) -or ([IO.FileInfo]::new($longPath)).Length -ne $item.size_bytes) { $missing=$true }
    }
    if ($missing) {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Install Git for Windows and sign in to the private repository, then rerun.' }
        $saved=$ErrorActionPreference
        try { $ErrorActionPreference='Continue'; git lfs version 2>&1 | Out-Null; $hasLfs=$LASTEXITCODE -eq 0 } finally { $ErrorActionPreference=$saved }
        if (-not $hasLfs) {
            $spec=(Get-Content (Join-Path $repoRoot 'runtime-bootstrap.json') -Raw | ConvertFrom-Json).git_lfs
            $archive=Join-Path $localDirectory 'git-lfs.zip'
            [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            if (-not (Test-Path $archive) -or (Get-FileHash $archive -Algorithm SHA256).Hash -ne $spec.sha256) {
                Invoke-WebRequest -Uri $spec.url -OutFile ($archive+'.partial') -UseBasicParsing -TimeoutSec 180
                if ((Get-FileHash ($archive+'.partial') -Algorithm SHA256).Hash -ne $spec.sha256) { throw 'Git LFS download checksum mismatch.' }
                Move-Item -LiteralPath ($archive+'.partial') -Destination $archive -Force
            }
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $zip=[IO.Compression.ZipFile]::OpenRead($archive)
            try { $entries=@($zip.Entries | Where-Object { $_.Name -eq 'git-lfs.exe' }); if ($entries.Count -ne 1) { throw 'Verified archive is missing git-lfs.exe.' }; [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0],(Join-Path $localDirectory 'git-lfs.exe'),$true) } finally { $zip.Dispose() }
            $env:PATH=$localDirectory+[IO.Path]::PathSeparator+$env:PATH
            # Keep Git status/pull working after the temporary PATH is restored.
            # Only repository-local filters are configured; custom hooks are preserved.
            $portableLfs=(Join-Path $localDirectory 'git-lfs.exe').Replace('\','/')
            foreach ($filter in @(@('clean','clean -- %f'),@('smudge','smudge -- %f'),@('process','filter-process'))) {
                git -c "safe.directory=$($repoRoot.Replace('\','/'))" -C $repoRoot config --local ("filter.lfs."+$filter[0]) ('"'+$portableLfs+'" '+$filter[1])
                if ($LASTEXITCODE -ne 0) { throw 'Could not configure the portable Git LFS filter for this checkout.' }
            }
            git -c "safe.directory=$($repoRoot.Replace('\','/'))" -C $repoRoot config --local filter.lfs.required true
            if ($LASTEXITCODE -ne 0) { throw 'Could not configure Git LFS for this checkout.' }
        }
        $saved=$ErrorActionPreference
        try { $ErrorActionPreference='Continue'; git -c "safe.directory=$($repoRoot.Replace('\','/'))" -c core.longpaths=true -C $repoRoot lfs pull '--include=data/assets/**,data/models/**,data/sources/trackwise_records.csv' '--exclude=' 2>&1 | ForEach-Object { Write-Host $_.ToString() }; $lfsExit=$LASTEXITCODE } finally { $ErrorActionPreference=$saved }
        if ($lfsExit -ne 0) { throw 'Git LFS retrieval failed. Check private-repository access and rerun.' }
    }
    Write-Host '[3/5] Building the MAP image (unchanged layers are cached)...'
    Invoke-Docker @('build','--platform','linux/amd64','--tag',$Image,$repoRoot)
    $mount="type=bind,source=$dataPath,target=/data"
    $prepare=@('run','--rm','--init','--no-healthcheck','--platform','linux/amd64','--env-file',$runtimeEnv,'--mount',$mount,'--mount',("type=bind,source="+(Join-Path $repoRoot 'data')+",target=/bundle,readonly"))
    if ($Source) { $sourcePath=ProjectPath $Source; if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) { throw "Source file not found: $sourcePath" }; $containerSource='/input/source'+[IO.Path]::GetExtension($sourcePath); $prepare+=@('--mount',"type=bind,source=$sourcePath,target=$containerSource,readonly") }
    $prepare+=@($Image,'python','-u','-m','capa_map.docker_setup','prepare')
    if ($Source) { $prepare+=@('--source',$containerSource) }; if ($Sheet) { $prepare+=@('--sheet',$Sheet) }
    Write-Host '[4/5] Preparing source and testing real models inside Docker...'
    Invoke-Docker $prepare
    if ($PrepareOnly) { Write-Host 'Preparation passed. No backend or paid pipeline was started.'; return }
    $sourceState=Get-Content -LiteralPath (Join-Path $dataPath '.setup/source.json') -Raw | ConvertFrom-Json
    $configuration['CAPA_SOURCE_SNAPSHOT']=[string]$sourceState.CAPA_SOURCE_SNAPSHOT; $configuration['CAPA_SOURCE_CSV']=[string]$sourceState.CAPA_SOURCE_CSV
    Write-RuntimeEnv
    $configId=HashText ((Get-Content $runtimeEnv -Raw)+$dataPath+[string]$Port)
    $imageId=(Invoke-Docker @('image','inspect','--format','{{.Id}}',$Image) -Capture).Trim()
    if ($existing -and ($existing.Image -ne $imageId -or $existing.Config.Labels.'capa.map.configuration' -ne $configId)) {
        if ($existing.State.Running) { Invoke-Docker @('exec',$ContainerName,'python','-m','capa_map.docker_setup','active-check'); Invoke-Docker @('stop','-t','30',$ContainerName) }
        Invoke-Docker @('container','rm',$ContainerName); $existing=$null
    }
    Write-Host '[5/5] Starting/reusing the MAP container...'
    if (-not $existing) {
        $networks=Invoke-Docker @('network','ls','--filter','name=^capa-ai$','--format','{{.Name}}') -Capture
        if (-not $networks) { Invoke-Docker @('network','create','capa-ai') }
        Invoke-Docker @('run','--detach','--init','--restart','unless-stopped','--platform','linux/amd64','--name',$ContainerName,'--network','capa-ai','--label',"capa.map.project=$projectId",'--label',"capa.map.configuration=$configId",'--publish',('127.0.0.1:'+$Port+':8101'),'--env-file',$runtimeEnv,'--mount',$mount,$Image)
    } elseif (-not $existing.State.Running) { Invoke-Docker @('start',$ContainerName) }
    $ready=$false
    for ($attempt=0; $attempt -lt 60; $attempt++) { Invoke-Docker @('exec',$ContainerName,'python','/opt/capa/healthcheck.py','--path','/api/v1/map/ready') -Capture -AllowFailure | Out-Null; if ($dockerExitCode -eq 0) { $ready=$true; break }; Start-Sleep -Seconds 1 }
    if (-not $ready) { throw "MAP did not become ready. Run: docker logs $ContainerName" }
    Write-Host "Backend ready: http://127.0.0.1:$Port/docs"
    if ($ServeOnly) { Write-Host 'No new analysis was submitted.'; return }
    $monitor=@('exec',$ContainerName,'python','-u','-m','capa_map.docker_setup','run','--limit',[string]$Limit)
    if ($NewRun) { $monitor+='--new-run' }
    Invoke-Docker $monitor
    Write-Host "Ready for the frontend. API: http://127.0.0.1:$Port | Data/results/notes: $dataPath"
} finally { $env:PATH=$originalPath; if ($lock) { $lock.Dispose() } }