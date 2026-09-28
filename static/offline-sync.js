// Uploads workouts the service worker kept on this device while offline, and
// shows where they stand. Loaded on every page while someone is signed in.
(function () {
  const store = self.offlineWorkouts;
  const me = (document.currentScript && document.currentScript.dataset.user) || '';
  if (!store || !me || !('indexedDB' in window)) return;

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const alerts = document.getElementById('client-alerts');
  const status = document.getElementById('offline-status');

  const dayLabel = (savedAt) => new Date(savedAt).toLocaleDateString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short',
  });

  const NOTICES_KEY = 'offline-workouts-notices';
  const loadedAt = Date.now();
  let touched = false;
  ['pointerdown', 'keydown'].forEach((type) => {
    window.addEventListener(type, () => { touched = true; }, { once: true, capture: true });
  });
  let shown = [];

  function notice(kind, message, link) {
    shown.push({ kind, message, link });
    if (!alerts) return;
    const alert = document.createElement('div');
    alert.className = `alert alert-${kind} alert-dismissible fade show`;
    alert.setAttribute('role', 'alert');
    alert.append(message);
    if (link) {
      const a = document.createElement('a');
      a.className = 'alert-link ms-2';
      a.href = link.href;
      a.textContent = link.label;
      alert.append(a);
    }
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'btn-close btn-close-white';
    close.setAttribute('data-bs-dismiss', 'alert');
    close.setAttribute('aria-label', 'Close');
    alert.append(close);
    alerts.append(alert);
  }

  const mine = () => store.all().then((items) => items.filter((item) => item.user === me));

  // Shows where kept workouts stand; returns how many are waiting to upload.
  async function render() {
    const items = await mine().catch(() => []);
    const failed = items.filter((item) => item.status === 'failed');
    const waiting = items.length - failed.length;
    if (!status) return waiting;
    status.replaceChildren();
    if (failed.length) {
      const a = document.createElement('a');
      a.className = 'offline-status__item offline-status__item--fix';
      a.href = `/log#fix-${failed[0].id}`;
      a.innerHTML = '<i class="bi bi-exclamation-circle"></i>';
      a.append(failed.length === 1 ? '1 workout needs a fix' : `${failed.length} workouts need a fix`);
      status.append(a);
    }
    if (waiting) {
      const span = document.createElement('span');
      span.className = 'offline-status__item';
      span.innerHTML = '<i class="bi bi-cloud-arrow-up"></i>';
      span.append(waiting === 1 ? '1 workout waiting to upload' : `${waiting} workouts waiting to upload`);
      status.append(span);
    }
    return waiting;
  }

  let csrfToken = csrfMeta ? csrfMeta.content : '';
  const send = (item) => fetch('/api/offline-workouts', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify({ text: item.text, saved_at: item.savedAt, user: item.user }),
  });

  async function upload(item) {
    let response;
    try {
      response = await send(item);
      if (response.status === 400) {
        // This page was opened offline and its security token is stale.
        const fresh = await fetch('/api/csrf-token', { credentials: 'same-origin' });
        if (!fresh.ok) return false;
        csrfToken = (await fresh.json()).token || '';
        response = await send(item);
      }
    } catch (err) {
      return false; // still offline
    }
    const body = await response.json().catch(() => ({}));
    // The day it was filed under (a dated title wins), else the day it was kept.
    const day = dayLabel(body.date ? new Date(`${body.date}T12:00`) : item.savedAt);
    if (response.ok && body.status === 'saved') {
      await store.remove(item.id);
      notice('success', `Your ${day} workout is uploaded.`, { href: body.url, label: 'View' });
      return 'uploaded';
    }
    if (response.ok && body.status === 'already_there') {
      await store.remove(item.id);
      notice('info', `${day} already had a workout, so the one saved offline wasn't added.`, { href: body.url, label: 'View' });
      return 'uploaded';
    }
    if (response.status === 422) {
      await store.put({ ...item, status: 'failed', error: body.error || '' });
      notice('warning', `Your ${day} workout couldn't be read.`, { href: `/log#fix-${item.id}`, label: 'Fix it' });
      return 'kept';
    }
    return false; // signed out, stale page, or server busy: try again later
  }

  // Just opened and untouched, the page is refreshed once so it shows the
  // uploaded workouts; the notices come along.
  function refreshWithNotices() {
    if (touched || Date.now() - loadedAt > 4000 || location.pathname === '/log') return false;
    try {
      sessionStorage.setItem(NOTICES_KEY, JSON.stringify(shown));
    } catch (err) {
      return false;
    }
    location.reload();
    return true;
  }

  function showCarriedNotices() {
    let carried = [];
    try {
      carried = JSON.parse(sessionStorage.getItem(NOTICES_KEY) || '[]');
      sessionStorage.removeItem(NOTICES_KEY);
    } catch (err) {
      return;
    }
    carried.forEach((n) => notice(n.kind, n.message, n.link));
  }

  let syncing = false;
  let retryTimer = null;
  let announceKept = false;
  async function sync() {
    if (syncing) return;
    let uploaded = 0;
    if (navigator.onLine !== false) {
      syncing = true;
      await tryUploads().then((count) => { uploaded = count; }).finally(() => { syncing = false; });
    }
    if (uploaded && refreshWithNotices()) return;
    const waiting = await render();
    if (announceKept) {
      // Only if it didn't go up straight away.
      announceKept = false;
      if (waiting) notice('success', "Saved on this phone. It'll upload when you're back online.");
    }
    // Signal can come back without the phone ever saying it was offline.
    if (waiting && !retryTimer) {
      retryTimer = setTimeout(() => {
        retryTimer = null;
        sync();
      }, 60000);
    }
  }

  async function tryUploads() {
    let uploaded = 0;
    try {
      const work = async () => {
        const items = (await mine()).filter((item) => item.status !== 'failed');
        for (const item of items) {
          const result = await upload(item);
          if (!result) break;
          if (result === 'uploaded') uploaded += 1;
        }
      };
      // One tab at a time, so two open tabs don't send the same workout.
      if (navigator.locks) await navigator.locks.request('offline-workouts-upload', work);
      else await work();
    } catch (err) {
      // Storage unavailable; nothing to upload.
    }
    return uploaded;
  }

  // The log page: confirm a workout kept offline, or open one that needs a fix.
  async function logPage() {
    const form = document.querySelector('form[action="/log"]');
    const textarea = form && form.querySelector('textarea[name="workout_text"]');
    if (!textarea) return;
    const hash = location.hash;
    if (hash === '#saved-offline') {
      history.replaceState(null, '', location.pathname + location.search);
      announceKept = true;
      return;
    }
    if (!hash.startsWith('#fix-')) return;
    history.replaceState(null, '', location.pathname + location.search);
    const item = await store.get(hash.slice(5)).catch(() => null);
    if (!item || item.user !== me) return;

    textarea.value = item.text;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    const addHidden = (name, value) => {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      input.value = value;
      form.append(input);
    };
    addHidden('offline_id', item.id);
    addHidden('saved_at', String(item.savedAt));

    const context = document.createElement('div');
    context.className = 'offline-fix';
    const what = document.createElement('span');
    what.textContent = `Saved offline ${dayLabel(item.savedAt)}${item.error ? ` · ${item.error}` : ''}`;
    const discard = document.createElement('button');
    discard.type = 'button';
    discard.className = 'offline-fix__discard';
    discard.textContent = 'Discard';
    discard.addEventListener('click', async () => {
      if (!window.confirm('Discard this workout? It was never uploaded.')) return;
      await store.remove(item.id);
      form.querySelectorAll('input[name="offline_id"], input[name="saved_at"]').forEach((el) => el.remove());
      textarea.value = '';
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      context.remove();
      render();
    });
    context.append(what, discard);
    form.prepend(context);
  }

  showCarriedNotices();
  logPage().catch(() => {}).then(sync);
  window.addEventListener('online', sync);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') sync();
  });
})();
