// Bridge for the Goals & reminders window (goals.html). See goals-window.js.
const { contextBridge, ipcRenderer } = require('electron');

const call = (ch) => (arg) => ipcRenderer.invoke(ch, arg);
contextBridge.exposeInMainWorld('goalsApi', {
  get: call('goals-get'),
  saveGoal: call('goals-save-goal'),
  deleteGoal: call('goals-delete-goal'),
  log: call('goals-log'),
  unlog: call('goals-unlog'),
  savePerson: call('goals-save-person'),
  deletePerson: call('goals-delete-person'),
  contacted: call('goals-contacted'),
  saveSettings: call('goals-save-settings'),
  setCharacter: call('goals-set-character'),
});
