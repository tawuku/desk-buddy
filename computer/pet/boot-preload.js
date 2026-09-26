// Bridge for the boot intro windows (computer/screen/boot.html): live
// startup status from the pet's main process. See main.js's showBoot.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisBoot', {
  onStatus: (callback) => ipcRenderer.on('boot-status', (_event, status) => callback(status)),
  done: () => ipcRenderer.send('boot-done'),
});
