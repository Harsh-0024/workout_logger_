// Workouts kept on this device while offline, until they upload. Shared by the
// service worker (which keeps them) and the pages (which upload them).
// Each item: { id, user, text, savedAt, status: 'pending' | 'failed', error }.
(function (scope) {
  const DB_NAME = 'workout-tracker-offline';
  const STORE = 'workouts';

  function openDb() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: 'id' });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function run(mode, work) {
    return openDb().then((db) => new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const request = work(tx.objectStore(STORE));
      tx.oncomplete = () => {
        db.close();
        resolve(request ? request.result : undefined);
      };
      tx.onerror = tx.onabort = () => {
        db.close();
        reject(tx.error);
      };
    }));
  }

  scope.offlineWorkouts = {
    all: () => run('readonly', (store) => store.getAll())
      .then((items) => (items || []).sort((a, b) => a.savedAt - b.savedAt)),
    get: (id) => run('readonly', (store) => store.get(id)),
    put: (item) => run('readwrite', (store) => { store.put(item); }),
    remove: (id) => run('readwrite', (store) => { store.delete(id); }),
  };
})(self);
