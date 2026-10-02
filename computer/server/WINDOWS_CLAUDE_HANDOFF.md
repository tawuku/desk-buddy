# Task for Claude on the Windows PC: move JARVIS's "brain" here

You are Claude running on the owner's Windows PC. Complete the move below, run
the commands yourself where you can, and finish by telling the owner, in plain
steps, exactly what *they* still need to do on their Mac.

## Goal
JARVIS (a voice assistant + desktop pet) currently runs entirely on the owner's
Mac. Move the heavy parts to this Windows PC:

- language model (llama.cpp, Qwen3-4B-Instruct)
- speech-to-text (whisper.cpp)
- voice (Piper, `en_GB-northern_english_male-medium`)

The Mac keeps only: wake word ("wake up Jarvis"), microphone, speaker, pet,
screen, lock-screen check. It talks to this PC over the home network on one
token-protected port, **8090**. Everything is already written and pushed; it
has just never been run on Windows, so expect to debug it.

Repo: https://github.com/tawuku/desk-buddy   branch: `main-wk3p4r`
Read `computer/server/README.md` first.

## Rules
- Never commit personal data (tokens, `config/server.json`, `config/remote.json`,
  `models/`, `logs/`, `engines/`, home paths, emails). They are git-ignored; keep
  it that way and check `git status` before any commit.
- If you fix a script, commit with a clear message and `git push origin main-wk3p4r`.
- Don't expose anything beyond port 8090 on the private network.

## Steps
1. **Prerequisites.** Check `git`, `python` (3.12; the script installs it with
   winget if missing) and that PowerShell can reach github.com and huggingface.co.
2. **Get the code.**
   ```powershell
   git clone https://github.com/tawuku/desk-buddy
   cd desk-buddy
   git checkout main-wk3p4r
   ```
3. **Run setup** (as Administrator so the firewall rule is created; add `-Gpu`
   if `nvidia-smi` shows an NVIDIA card):
   ```powershell
   powershell -ExecutionPolicy Bypass -File computer\server\setup-windows.ps1
   ```
   It downloads a few GB, installs Piper in `computer\voice\venv-win`, fetches
   the llama.cpp and whisper.cpp Windows builds into `engines\win`, writes
   `config\server.json` (with a random token), opens the firewall for the local
   subnet only, registers the "JARVIS brain" start-at-login task and starts it.
   It is safe to re-run.
4. **Likely failure points; fix them in the script if hit:**
   - `whisper-server.exe` missing from the whisper.cpp zip: find the right
     release asset on github.com/ggml-org/whisper.cpp and adjust the pattern in
     `setup-windows.ps1`.
   - llama.cpp / whisper.cpp asset names changed: adjust the regex in `Get-Zip`.
   - `piper-tts` install or voice download fails.
   - Firewall rule not created: re-run as Administrator.
5. **Verify the server** (first start takes about a minute while the model loads):
   ```powershell
   $c = Get-Content config\server.json | ConvertFrom-Json
   Invoke-RestMethod http://localhost:8090/health -Headers @{Authorization="Bearer $($c.token)"}
   ```
   If it fails, read `logs\server.log`, `logs\llama.err.log`, `logs\whisper.err.log`,
   and run `computer\server\start-server.ps1` in a visible window to see errors.
   Also check that `/stt`, `/llm` and `/tts` respond (see `jarvis_server.py`).
6. **Power:** set sleep to Never while plugged in
   (`powercfg /change standby-timeout-ac 0`), otherwise JARVIS dies when the PC sleeps.
7. **Get the connection details:** the PC's LAN IP (192.168.x.x / 10.x.x.x) and
   the token from `config\server.json`. A reserved/static IP for this PC in the
   router is recommended so the address doesn't change.

## What to tell the owner at the end
Give them this, filled in with the real IP and token (tell them to keep the
token private):

1. On the Mac, in the desk-buddy folder: `git pull`
2. `bash computer/server/connect.sh <PC-IP> <TOKEN>`
   (it tests the connection, then restarts JARVIS in remote mode)
3. `bash computer/server/connect.sh status` should say the remote brain is reachable.
4. Say "wake up Jarvis" and ask something; the reply should come from the PC.
5. To go back to running on the Mac: `bash computer/server/connect.sh off`
6. The PC must be on, awake and on the same Wi-Fi/network whenever JARVIS is used.

Also report: what you changed or fixed, anything still broken, and whether the
boot-key sequence on the Mac still needs a physical test (it does; only the
owner can press the key).
