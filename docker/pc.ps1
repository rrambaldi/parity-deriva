<#
parity-deriva on this PC, in Docker. One folder holds it all: the code in
<Root>\app, and everything the server keeps in <Root>\data - the runs, the
market data, the .env - mounted as the container's /data. Back that one up.

    double-click install-pc.cmd, beside this file, or
    powershell -ExecutionPolicy Bypass -File pc.ps1
    powershell -ExecutionPolicy Bypass -File pc.ps1 -Root D:\parity -Branch main

What is missing it installs first, with winget: Git, WSL 2 (then a restart and
a second run), Docker Desktop, which it starts and waits for. The steps for a
person are in docs/INSTALLA-PC.md.

The first time it opens the setup with its code already in: who may open the
pages, what this server does, and the Archive it takes everything from. Run it
again to update: it pulls the code, builds the image again and starts it on the
same data.

The GitHub account it clones with needs access to the repository.
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

$onWindows = $env:OS -eq 'Windows_NT'

function Must([scriptblock]$Do, [string]$Said) {
	& $Do
	if ($LASTEXITCODE -ne 0) { throw $Said }
}

function Has([string]$Tool) { [bool](Get-Command $Tool -ErrorAction SilentlyContinue) }

# what an installer put on the PATH, without opening a new window
function ReadPath {
	$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
		[Environment]::GetEnvironmentVariable('Path', 'User')
}

function Install([string]$Id, [string]$Name) {
	if (-not (Has 'winget')) {
		throw "$Name is missing, and so is winget to install it: install App Installer from the Microsoft Store, then run this again."
	}
	Write-Host "Installing $Name. If Windows asks whether to allow changes, answer Yes."
	Must { winget install -e --id $Id --accept-package-agreements --accept-source-agreements } "$Name did not install: see above"
	ReadPath
}

if ($onWindows) {
	if (-not (Has 'git')) { Install 'Git.Git' 'Git' }
	if (-not (Has 'docker')) {
		# Docker Desktop runs on WSL 2, which needs a restart once turned on
		$wsl = $false
		if (Has 'wsl') {
			wsl --status *> $null
			$wsl = $LASTEXITCODE -eq 0
		}
		if (-not $wsl) {
			Write-Host 'Turning on WSL 2, which Docker Desktop runs on. If Windows asks whether to allow changes, answer Yes.'
			Start-Process wsl -ArgumentList '--install', '--no-distribution' -Verb RunAs -Wait
			Write-Host ''
			Write-Host 'Now restart the PC, then double-click install-pc.cmd again.'
			exit 3
		}
		Install 'Docker.DockerDesktop' 'Docker Desktop'
	}
}
foreach ($tool in 'git', 'docker') {
	if (-not (Has $tool)) { throw "$tool is missing: install it, then run this again." }
}

docker info *> $null
if ($LASTEXITCODE -ne 0 -and $onWindows) {
	$desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
	if (Test-Path $desktop) {
		Write-Host 'Starting Docker Desktop. The first time it shows its terms: click Accept; asked to sign in, skip it.'
		Start-Process $desktop
		for ($i = 0; $i -lt 60; $i++) {
			Start-Sleep -Seconds 5
			docker info *> $null
			if ($LASTEXITCODE -eq 0) { break }
		}
	}
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
	throw 'Docker Desktop does not answer. Just installed? Sign out of Windows and in again, open Docker Desktop, wait for "Engine running", then run this again.'
}

$app = Join-Path $Root 'app'
$data = Join-Path $Root 'data'
New-Item -ItemType Directory -Force -Path $data -ErrorAction Stop | Out-Null

if (Test-Path (Join-Path $app '.git')) {
	Must { git -C $app fetch origin $Branch } 'git fetch failed'
	Must { git -C $app checkout $Branch } 'git checkout failed'
	Must { git -C $app pull --ff-only origin $Branch } 'git pull failed: are there changes of yours in the app folder?'
} else {
	Write-Host 'Taking the code from GitHub. If a window asks you to sign in to GitHub, do it: Sign in with your browser.'
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
	Write-Host 'Building and starting parity-deriva: the first time it takes a few minutes.'
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
