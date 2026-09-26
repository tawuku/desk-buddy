const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('panel', {
  onData: (callback) => {
    ipcRenderer.on('panel-data', (_event, data) => callback(data));
  },
  onState: (callback) => {
    ipcRenderer.on('panel-state', (_event, data) => callback(data));
  },
  dismiss: () => ipcRenderer.send('panel-dismiss'),
  resize: (height) => ipcRenderer.send('panel-resize', height),
});
