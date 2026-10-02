# Starts the JARVIS brain: llama-server + whisper-server (both on this PC's
# loopback) and jarvis_server.py (the one LAN port, token-protected).
# Run at login by the "JARVIS brain" scheduled task made by setup-windows.ps1.
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$cfg  = Get-Content (Join-Path $Root "config\server.json") | ConvertFrom-Json
$log  = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $log | Out-Null
Get-Process llama-server, whisper-server -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "jarvis_server" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

foreach ($n in "JARVIS_LLM_API_KEY", "JARVIS_LLM_URL", "JARVIS_LLM_MODEL") {
  $v = [Environment]::GetEnvironmentVariable($n, "User"); if ($v) { Set-Item "Env:$n" $v }
}
$threads = [Math]::Max(2, [Environment]::ProcessorCount - 2)
$ngl = if ($cfg.gpu) { @("-ngl", "99") } else { @() }
# With JARVIS_LLM_API_KEY set the language model is hosted, so skip the local one
# (frees the CPU for speech recognition and voice).
if (-not [Environment]::GetEnvironmentVariable("JARVIS_LLM_API_KEY", "User")) {
Start-Process -WindowStyle Hidden $cfg.llama -ArgumentList (@("--model", $cfg.model, "--alias", "qwen3-4b-instruct",
  "--host", "127.0.0.1", "--port", "8080", "--ctx-size", "4096", "--parallel", "1", "--threads", $threads, "--no-webui") + $ngl) `
  -RedirectStandardOutput "$log\llama.out.log" -RedirectStandardError "$log\llama.err.log"
}
Start-Process -WindowStyle Hidden $cfg.whisper -ArgumentList @("-m", (Join-Path $Root "models\ggml-base.en.bin"),
  "--host", "127.0.0.1", "--port", "8093", "-t", "4") `
  -RedirectStandardOutput "$log\whisper.out.log" -RedirectStandardError "$log\whisper.err.log"
& (Join-Path $Root "computer\voice\venv-win\Scripts\python.exe") (Join-Path $Root "computer\server\jarvis_server.py") *>> "$log\server.log"
