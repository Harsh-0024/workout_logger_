const CACHE_VERSION = 'workout-tracker-v3';
const OFFLINE_URL = '/static/offline.html';

const CORE_ASSETS = [
  '/',
  '/log',
  '/stats',
  '/retrieve/categories',
  '/static/manifest.json'
];

// Only keep real answers: a redirect (e.g. to /login) can't be replayed for a
// navigation, and error pages shouldn't stand in for the page offline.
// Cross-origin CDN files load as opaque responses, which are fine to keep.
function cacheable(response) {
  return response && !response.redirected && (response.ok || response.type === 'opaque');
}

function remember(request, response) {
  if (!cacheable(response)) return;
  const copy = response.clone();
  caches.open(CACHE_VERSION)
    .then((cache) => cache.put(request, copy))
    .catch(() => {});
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) =>
      Promise.all([
        cache.add(OFFLINE_URL),
        ...CORE_ASSETS.map((url) =>
          fetch(url)
            .then((response) => (cacheable(response) ? cache.put(url, response) : null))
            .catch(() => null)
        ),
      ])
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => (key === CACHE_VERSION ? null : caches.delete(key))))
    )
  );
  self.clients.claim();
});

// Network first for everything, so pages and data are never stale while
// online; the cache only answers when the network can't.
self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET' || !request.url.startsWith('http')) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        remember(request, response);
        return response;
      })
      .catch(() =>
        caches.match(request).then((cached) => {
          if (cached) return cached;
          if (request.mode === 'navigate') return caches.match(OFFLINE_URL);
          return Response.error();
        })
      )
  );
});
