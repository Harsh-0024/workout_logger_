const VERSION = 'v3';
// Files that are the same for everyone (styles, icons, the offline page).
const SHARED_CACHE = `workout-tracker-shared-${VERSION}`;
// The signed-in person's pages and data; emptied when nobody is signed in.
const USER_CACHE = `workout-tracker-user-${VERSION}`;
const OFFLINE_URL = '/static/offline.html';
// Where '/' sends the signed-in person, so the installed app can open offline.
const HOME_KEY = '/__sw/home';

const CORE_PAGES = ['/log', '/stats', '/retrieve/categories'];

function cacheNameFor(url) {
  const { origin, pathname } = new URL(url, self.location.origin);
  if (origin !== self.location.origin) return SHARED_CACHE;
  if (pathname.startsWith('/static/') || pathname.startsWith('/app-icon/')) return SHARED_CACHE;
  return USER_CACHE;
}

// Only keep real answers: a redirect (e.g. to /login) can't be replayed for a
// navigation, and error pages shouldn't stand in for the page offline.
// Cross-origin CDN files load as opaque responses, which are fine to keep.
function cacheable(response) {
  return response && !response.redirected && (response.ok || response.type === 'opaque');
}

function remember(request, response) {
  if (!cacheable(response)) return Promise.resolve();
  const copy = response.clone();
  return caches.open(cacheNameFor(request.url))
    .then((cache) => cache.put(request, copy))
    .catch(() => {});
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    Promise.all([
      caches.open(SHARED_CACHE).then((cache) => cache.addAll([OFFLINE_URL, '/static/manifest.json'])),
      ...CORE_PAGES.map((url) =>
        fetch(url).then((response) => remember(new Request(url), response)).catch(() => null)
      ),
    ])
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  const keep = [SHARED_CACHE, USER_CACHE];
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => (keep.includes(key) ? null : caches.delete(key))))
    )
  );
  self.clients.claim();
});

// Every page tells the worker who is signed in (see base.html).
self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'signed-in' && typeof data.home === 'string') {
    event.waitUntil(
      caches.open(USER_CACHE).then((cache) => cache.put(HOME_KEY, new Response(data.home)))
    );
  } else if (data.type === 'signed-out') {
    event.waitUntil(caches.delete(USER_CACHE));
  }
});

function offlineNavigation(request) {
  return caches.match(request).then((cached) => {
    if (cached) return cached;
    if (new URL(request.url).pathname === '/') {
      return caches.open(USER_CACHE)
        .then((cache) => cache.match(HOME_KEY))
        .then((home) => (home ? home.text() : null))
        .then((home) => (home ? Response.redirect(home, 302) : caches.match(OFFLINE_URL)));
    }
    return caches.match(OFFLINE_URL);
  });
}

// Network first for everything, so pages and data are never stale while
// online; the cache only answers when the network can't.
self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET' || !request.url.startsWith('http')) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        event.waitUntil(remember(request, response));
        return response;
      })
      .catch(() => {
        if (request.mode === 'navigate') return offlineNavigation(request);
        return caches.match(request).then((cached) => cached || Response.error());
      })
  );
});
