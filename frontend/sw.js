/* Replay PWA service worker.
 * 只缓存 App 壳子（HTML/JS/CSS/图标），/api/*（含录像 Range 分片流）永远直通网络，绝不缓存。
 */
const SHELL_CACHE = 'replay-shell-v3';
// 壳子文件增长封顶：发版时 ?v= 变化会产生新条目，超量时按插入顺序淘汰最旧的。
const MAX_SHELL_ENTRIES = 60;

function shellPath(pathname) {
  if (['/app.js', '/style.css', '/live.js', '/live.css'].includes(pathname)) return true;
  if (pathname === '/manifest.webmanifest') return true;
  if (pathname === '/static/app.js' || pathname === '/static/app.css') return true;
  if (pathname.startsWith('/icons/')) return true;
  return false;
}

// SPA 页面路径：导航请求走网络优先，离线时回退到缓存的壳子。
function isPagePath(pathname) {
  if (pathname === '/' || pathname === '/index.html') return true;
  if (pathname === '/streamers' || pathname === '/favorites') return true;
  if (pathname.startsWith('/streamer/')) return true;
  return false;
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then(cache =>
        cache.addAll(['/', '/app.js?v=9', '/style.css?v=9', '/live.js?v=1', '/live.css?v=1', '/manifest.webmanifest', '/icons/icon-192.png'])
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches
      .keys()
      .then(keys => Promise.all(keys.filter(key => key !== SHELL_CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

async function trimShell(cache) {
  const keys = await cache.keys();
  if (keys.length > MAX_SHELL_ENTRIES) {
    await Promise.all(keys.slice(0, keys.length - MAX_SHELL_ENTRIES).map(key => cache.delete(key)));
  }
}

self.addEventListener('fetch', event => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // API 与媒体流（含 Range 请求）：直通网络，绝不缓存。
  if (url.pathname.startsWith('/api/') || request.headers.has('range')) {
    return;
  }

  if (request.mode === 'navigate') {
    // 页面导航：优先网络，离线时回退到缓存的壳子。
    // 非页面路径（如直接打开 /healthz）不接管，避免把非 HTML 响应污染壳子缓存。
    if (!isPagePath(url.pathname)) return;
    event.respondWith(
      fetch(request)
        .then(response => {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then(cache => cache.put('/', copy));
          return response;
        })
        .catch(() => caches.match('/'))
    );
    return;
  }

  if (!shellPath(url.pathname)) return;

  // 壳子静态资源：缓存优先，后台更新。
  event.respondWith(
    caches.match(request).then(cached => {
      const network = fetch(request).then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then(cache => cache.put(request, copy).then(() => trimShell(cache)));
        }
        return response;
      });
      return cached || network;
    })
  );
});
