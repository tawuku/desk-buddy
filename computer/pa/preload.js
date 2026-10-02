const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pa', {
  getStatus: () => ipcRenderer.invoke('pa:get-status'),
  start: (key) => ipcRenderer.invoke('pa:start', key),
  stop: (key) => ipcRenderer.invoke('pa:stop', key),
  restart: (key) => ipcRenderer.invoke('pa:restart', key),
  startJarvis: () => ipcRenderer.invoke('pa:start-jarvis'),
  stopJarvis: () => ipcRenderer.invoke('pa:stop-jarvis'),
  getVoiceOptions: () => ipcRenderer.invoke('pa:get-voice-options'),
  setVoice: (opts) => ipcRenderer.invoke('pa:set-voice', opts),
  previewVoice: (opts) => ipcRenderer.invoke('pa:preview-voice', opts),
  getThreads: () => ipcRenderer.invoke('pa:get-threads'),
  setThreads: (n) => ipcRenderer.invoke('pa:set-threads', n),
  openLogs: () => ipcRenderer.invoke('pa:open-logs'),
  openScreen: (autostart) => ipcRenderer.invoke('pa:open-screen', autostart),
  openGoals: () => ipcRenderer.invoke('pa:open-goals'),
  openChat: () => ipcRenderer.invoke('pa:open-chat'),
});
