// Bridge for the JARVIS screen windows (pages served by the JARVIS app on
// 127.0.0.1:8094): minimize / restore / close go through the pet, which owns
// the windows. See main.js's showScreens / setScreenMode.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisScreen', {
  setMode: (mode) => ipcRenderer.send('screen-mode', mode), // 'mini' | 'full'
  close: () => ipcRenderer.send('screen-close'),
  onMode: (callback) => ipcRenderer.on('screen-mode', (_event, mode) => callback(mode)),
});
