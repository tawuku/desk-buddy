// Bridge for the chat window (computer/screen/chat.html, served by JARVIS):
// Esc hides it; it's told when it's shown again so the input gets focus.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisChat', {
  hide: () => ipcRenderer.send('chat-hide'),
  onShow: (callback) => ipcRenderer.on('chat-shown', () => callback()),
});
