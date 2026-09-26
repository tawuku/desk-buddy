// Bridge for the reminder card above the pet (popup.html). See reminders.js.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('popup', {
  onCard: (callback) => ipcRenderer.on('popup-card', (_event, card) => callback(card)),
  act: (action) => ipcRenderer.invoke('popup-action', action),
  dismiss: () => ipcRenderer.send('popup-dismiss'),
  resize: (height) => ipcRenderer.send('popup-resize', height),
});
