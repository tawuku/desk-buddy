# Engines

`bin/` (not in git) can hold CPU-tuned builds of `llama-server`,
`whisper-server` and `whisper-cli`. If it's empty, JARVIS uses Homebrew's
`llama.cpp` and `whisper-cpp` (install.sh installs them).

To build your own, faster on older Intel Macs: compile llama.cpp / whisper.cpp
from source with `-DGGML_NATIVE=ON` and copy the binaries here.
