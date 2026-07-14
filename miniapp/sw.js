/**
 * Service Worker для офлайн-карт (Economy Premium).
 * Кэширует тайлы OpenStreetMap для использования без интернета.
 */

const CACHE_NAME = 'benzin-tiles-v1';
const TILE_URL_PATTERN = /\/tiles\//;

// Установка — кэшируем базовые ресурсы
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll([
        '/',
        '/index.html',
        '/style.css',
        '/app.js',
        '/premium-catalog.js',
        '/premium-ui.js',
      ]).catch(() => {});
    })
  );
  self.skipWaiting();
});

// Активация — удаляем старые кэши
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

// Перехват запросов — кэшируем тайлы
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Кэшируем только тайлы OpenStreetMap
  if (url.hostname.includes('tile.openstreetmap.org') ||
      url.hostname.includes('a.tile.openstreetmap.org') ||
      url.hostname.includes('b.tile.openstreetmap.org') ||
      url.hostname.includes('c.tile.openstreetmap.org')) {

    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;

        return fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, clone);
            });
          }
          return response;
        }).catch(() => {
          // Если офлайн и нет в кэше — возвращаем заглушку
          return new Response('', { status: 408, statusText: 'Offline' });
        });
      })
    );
    return;
  }

  // Для остальных запросов — network first
  event.respondWith(
    fetch(event.request).catch(() => {
      return caches.match(event.request);
    })
  );
});

// Сообщения от клиента
self.addEventListener('message', (event) => {
  if (event.data === 'clear-cache') {
    caches.delete(CACHE_NAME).then(() => {
      self.clients.matchAll().then((clients) => {
        clients.forEach((client) => client.postMessage('cache-cleared'));
      });
    });
  }
});
