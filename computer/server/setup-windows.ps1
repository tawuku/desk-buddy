<#
 JARVIS brain server -- one-time setup on the Windows PC.
 Run in PowerShell, from the repo folder (git clone it first):

   powershell -ExecutionPolicy Bypass -File computer\server\setup-windows.ps1
   powershell -ExecutionPolicy Bypass -File computer\server\setup-windows.ps1 -Gpu   # NVIDIA card

 Installs Python + the Piper voice, the llama.cpp and whisper.cpp Windows
 builds, the language/speech/voice models, opens the firewall port for your
 home network only, creates a start-at-login task, and prints the line to
 run on the Mac. Safe to re-run.
#>
param([switch]$Gpu, [string]$Model = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", [int]$Port = 8090)
$ErrorActionPreference = "Stop"
$Root   = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Engine = Join-Path $Root "engines\win"
$Models = Join-Path $Root "models"
New-Item -ItemType Directory -Force $Engine, $Models, (Join-Path $Models "piper"), (Join-Path $Root "logs") | Out-Null

# The progress bar makes Invoke-WebRequest crawl on Windows PowerShell 5.1.
$ProgressPreference = "SilentlyContinue"
# Download to a .part file (resumable with curl) and rename when complete, so an
# interrupted run never leaves a truncated file that looks finished.
function Get-File($url, $dest) {
  if (Test-Path $dest) { return }
  Write-Host "  downloading $(Split-Path $dest -Leaf)"
  $part = "$dest.part"
  & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $part $url
  if ($LASTEXITCODE -ne 0) { throw "download failed: $url" }
  Move-Item -Force $part $dest
}
# GitHub's "latest" release of these repos often has no Windows files, so take
# the newest release that does.
function Get-Asset($repo, $pattern) {
  $rels = Invoke-RestMethod "https://api.github.com/repos/$repo/releases?per_page=30" -Headers @{ "User-Agent" = "jarvis" }
  foreach ($r in $rels) { foreach ($a in $r.assets) { if ($a.name -match $pattern) { return $a } } }
  throw "no release of $repo has a file matching $pattern"
}
function Get-Zip($repo, $pattern, $into) {
  # already unpacked (a running server keeps its DLLs locked, so don't re-extract)
  if (Get-ChildItem -Recurse $into -Filter "*-server.exe" -ErrorAction SilentlyContinue) { return }
  $a = Get-Asset $repo $pattern
  $zip = Join-Path $env:TEMP $a.name
  Get-File $a.browser_download_url $zip
  Expand-Archive -Force $zip $into
}

Write-Host "[1/6] Python + git"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements; $env:Path += ";$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts" }
$Venv = Join-Path $Root "computer\voice\venv-win"
if (-not (Test-Path $Venv)) { python -m venv $Venv }
& "$Venv\Scripts\pip.exe" install --quiet piper-tts==1.8.0

Write-Host "[2/6] llama.cpp (language model engine)"
if ($Gpu) {
  Get-Zip "ggml-org/llama.cpp" "^llama-.*-bin-win-cuda-12\.4-x64\.zip$" "$Engine\llama"
  Get-Zip "ggml-org/llama.cpp" "^cudart-llama-bin-win-cuda-12\.4-x64\.zip$" "$Engine\llama"
} else {
  Get-Zip "ggml-org/llama.cpp" "^llama-.*-bin-win-cpu-x64\.zip$" "$Engine\llama"
}
Write-Host "[3/6] whisper.cpp (speech recognition engine)"
if ($Gpu) { Get-Zip "ggml-org/whisper.cpp" "^whisper-cublas-12\.4\.0-bin-x64\.zip$" "$Engine\whisper" }
else      { Get-Zip "ggml-org/whisper.cpp" "^whisper-bin-x64\.zip$" "$Engine\whisper" }
$llama   = (Get-ChildItem -Recurse $Engine\llama   -Filter llama-server.exe   | Select-Object -First 1).FullName
$whisper = (Get-ChildItem -Recurse $Engine\whisper -Filter whisper-server.exe | Select-Object -First 1).FullName
if (-not $llama)   { throw "llama-server.exe not found in the download" }
if (-not $whisper) { throw "whisper-server.exe not found in the download (check engines\win\whisper)" }

Write-Host "[4/6] models (a few GB, once)"
Get-File "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/$Model" (Join-Path $Models $Model)
Get-File "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" (Join-Path $Models "ggml-base.en.bin")
$v = "en_GB-northern_english_male-medium"
foreach ($ext in "onnx", "onnx.json") {
  Get-File "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/$v.$ext" (Join-Path $Models "piper\$v.$ext")
}

Write-Host "[5/6] token + settings"
$cfgFile = Join-Path $Root "config\server.json"
if (Test-Path $cfgFile) { $cfg = Get-Content $cfgFile | ConvertFrom-Json } else {
  $bytes = New-Object byte[] 24; (New-Object Security.Cryptography.RNGCryptoServiceProvider).GetBytes($bytes)
  $cfg = [pscustomobject]@{ token = ([BitConverter]::ToString($bytes) -replace "-", "").ToLower(); port = $Port }
}
$cfg | Add-Member -Force -NotePropertyName llama   -NotePropertyValue $llama
$cfg | Add-Member -Force -NotePropertyName whisper -NotePropertyValue $whisper
$cfg | Add-Member -Force -NotePropertyName model   -NotePropertyValue (Join-Path $Models $Model)
$cfg | Add-Member -Force -NotePropertyName gpu     -NotePropertyValue ([bool]$Gpu)
$cfg | ConvertTo-Json | Set-Content $cfgFile

Write-Host "[6/6] firewall (home network only) + start at login"
Remove-NetFirewallRule -DisplayName "JARVIS brain" -ErrorAction SilentlyContinue
try {
  New-NetFirewallRule -DisplayName "JARVIS brain" -Direction Inbound -Protocol TCP -LocalPort $cfg.port -RemoteAddress LocalSubnet -Profile Private | Out-Null
} catch { Write-Host "  (couldn't add the firewall rule -- re-run this script as Administrator once)" -ForegroundColor Yellow }
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSScriptRoot\start-server.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
try {
  Register-ScheduledTask -TaskName "JARVIS brain" -Action $action -Trigger $trigger -Force -ErrorAction Stop | Out-Null
  Start-ScheduledTask -TaskName "JARVIS brain"
} catch {
  Write-Host "  (couldn't register the start-at-login task -- re-run as Administrator once; starting the server now instead)" -ForegroundColor Yellow
  Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-ExecutionPolicy Bypass -File `"$PSScriptRoot\start-server.ps1`""
}

# skip Hyper-V/WSL/VM adapters (e.g. 172.x "vEthernet"); the Mac can't reach those
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch "vEthernet|WSL|VirtualBox|VMware|Loopback" -and $_.IPAddress -match "^(192\.168|10\.|172\.(1[6-9]|2\d|3[01]))\." } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Done. Starting now (the first start loads the model, ~1 minute)." -ForegroundColor Green
Write-Host "On the Mac, run:" -ForegroundColor Green
Write-Host "  bash computer/server/connect.sh $ip $($cfg.token)"
Write-Host "Keep this PC from sleeping: Settings > System > Power > Sleep = Never (plugged in)."
