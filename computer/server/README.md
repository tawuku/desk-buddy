# JARVIS brain server (run the heavy parts on a Windows PC)

The language model, speech recognition and voice are the CPU-hungry parts of
JARVIS. This lets a stronger / always-on Windows PC do them while the Mac only
listens for the wake word, plays audio and shows the pet and screen.

```
Mac (mic, speaker, pet, screen)  --- home network, one token-protected port --->  Windows PC
  wake word + lock check                                                           llama.cpp  (answers)
  records your voice  -------------------- /stt ------------------------------->  whisper.cpp (speech -> text)
  asks questions      -------------------- /llm ------------------------------->  llama-server
  plays the reply     <------------------- /tts --------------------------------  Piper voice
```

Only port 8090 is exposed (private network only, needs the token). The model
servers stay on the PC's loopback.

## 1. On the Windows PC
```powershell
git clone https://github.com/tawuku/desk-buddy
cd desk-buddy
powershell -ExecutionPolicy Bypass -File computer\server\setup-windows.ps1        # add -Gpu for an NVIDIA card
```
It downloads a few GB (models), opens the firewall for your home network,
starts the server at every login, and prints the exact command for the Mac.
Set the PC's sleep to "Never" while plugged in.

## 2. On the Mac
```bash
bash computer/server/connect.sh <pc-ip> <token>
bash computer/server/connect.sh status      # is it reachable?
bash computer/server/connect.sh off         # back to everything on the Mac
```
The Mac then stops running the model and Whisper locally. If the PC is off,
JARVIS can't answer -- run `connect.sh off` to go back to local mode.

## Away from home (Tailscale)
Install [Tailscale](https://tailscale.com) on both the PC and the Mac and sign
in to the same account. Run `tailscale status` on the Mac to find the PC's
`100.x.x.x` address, then use it as `<pc-ip>` above. It works at home and
away, so you don't need to switch back and forth. If the Mac can't reach the PC,
allow port 8090 from `100.64.0.0/10` in the PC's Windows firewall:

```powershell
New-NetFirewallRule -DisplayName "JARVIS brain (Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 8090 -RemoteAddress 100.64.0.0/10 -Action Allow
```

Files: `config/server.json` (PC) and `config/remote.json` (Mac) hold the token
and are git-ignored. Logs on the PC: `logs\server.log`, `llama.err.log`.
