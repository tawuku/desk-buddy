const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pet', {
  onMood: (callback) => {
    ipcRenderer.on('mood-update', (_event, mood) => callback(mood));
  },
  moveWindowBy: (dx, dy) => ipcRenderer.send('move-window-by', { dx, dy }),
  getBounds: () => ipcRenderer.invoke('pet-bounds'),
  getCharacter: () => ipcRenderer.invoke('pet-get-character'),
  getUserName: () => ipcRenderer.invoke('pet-get-name'),
  onCharacter: (callback) => ipcRenderer.on('pet-character', (_event, c) => callback(c)),
  onReact: (callback) => ipcRenderer.on('pet-react', (_event, kind) => callback(kind)),
  onGreet: (callback) => ipcRenderer.on('pet-greet', (_event, text) => callback(text)),
});
