<#
parity-deriva on this PC, in Docker. One folder holds it all: the code in
<Root>\app, and everything the server keeps in <Root>\data - the runs, the
market data, the .env - mounted as the container's /data. Back that one up.

    powershell -ExecutionPolicy Bypass -File pc.ps1
    powershell -ExecutionPolicy Bypass -File pc.ps1 -Root D:\parity -Branch main

The first time it opens the setup with its code already in: who may open the
pages, what this server does, and the Archive it takes everything from. Run it
again to update: it pulls the code, builds the image again and starts it on the
same data.

Needs Docker Desktop, running, and Git for Windows with access to the repository.
#>
param(
	[string]$Root = (Join-Path $HOME 'parity-deriva'),
	[string]$Branch = 'dev',
	[string]$Repo = 'https://github.com/rrambaldi/parity-deriva.git'
)
# not $ErrorActionPreference = 'Stop': Windows PowerShell would take what docker
# and git write on stderr for errors; each step says its own failure instead
# docker-compose.yml publishes it on 127.0.0.1 only
$Url = 'http://localhost:8731'

function Must([scriptblock]$Do, [string]$Said) {
	& $Do
	if ($LASTEXITCODE -ne 0) { throw $Said }
}

foreach ($tool in 'git', 'docker') {
	if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
		throw "$tool is missing: winget install Git.Git, winget install Docker.DockerDesktop"
	}
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running: start it, wait until it says running, then run this again.' }

$app = Join-Path $Root 'app'
$data = Join-Path $Root 'data'
New-Item -ItemType Directory -Force -Path $data -ErrorAction Stop | Out-Null

if (Test-Path (Join-Path $app '.git')) {
	Must { git -C $app fetch origin $Branch } 'git fetch failed'
	Must { git -C $app checkout $Branch } 'git checkout failed'
	Must { git -C $app pull --ff-only origin $Branch } 'git pull failed: are there changes of yours in the app folder?'
} else {
	# LF endings as in the repository: the image's shell scripts do not run with CRLF
	Must { git clone --config core.autocrlf=false --branch $Branch $Repo $app } 'git clone failed'
}

# the data on this PC rather than in a Docker volume: Compose reads this file
# beside docker-compose.yml, and its /data replaces the volume's
$source = $data -replace '\\', '/'
$override = @"
# written by docker/pc.ps1: /data is $data on this PC
services:
  parity:
    volumes:
      - type: bind
        source: "$source"
        target: /data
"@
[IO.File]::WriteAllText((Join-Path $app 'docker-compose.override.yml'), $override)

Push-Location $app
try {
	Must { docker compose up -d --build } 'docker compose failed: see above'
	Write-Host 'Waiting for parity-deriva to start...'
	$code = $null
	$ready = $false
	for ($i = 0; $i -lt 90 -and -not $code -and -not $ready; $i++) {
		Start-Sleep -Seconds 2
		try { $access = Invoke-RestMethod "$Url/api/access" -TimeoutSec 5 -ErrorAction Stop } catch { continue }
		if (-not $access.setup) { $ready = $true; break }
		# the setup's code, the last one the log printed: each start makes a new one
		$log = (docker compose logs parity 2>&1) | Out-String
		$found = [regex]::Matches($log, 'parity-deriva setup code: ([A-Z0-9]{4}-[A-Z0-9]{4})')
		if ($found.Count) { $code = $found[$found.Count - 1].Groups[1].Value }
	}
} finally {
	Pop-Location
}

if ($code) {
	Start-Process "$Url/setup#code=$code"
	Write-Host "Setup open in the browser (code $code)."
} elseif ($ready) {
	Start-Process "$Url/"
	Write-Host 'Set up already: the app is open in the browser.'
} else {
	throw "parity-deriva did not answer on $Url. Look at its log: cd $app; docker compose logs parity"
}
Write-Host ''
Write-Host "app:   $Url"
Write-Host "code:  $app"
Write-Host "data:  $data   <- the folder to back up"
Write-Host "log:   cd $app; docker compose logs -f parity"
Write-Host "stop:  cd $app; docker compose stop"
