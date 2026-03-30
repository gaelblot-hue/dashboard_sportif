const CACHE_NAME = 'radar-v6-v1';
const URLS_TO_CACHE = [
    '/',
    '/index.html',
    'https://cdn.tailwindcss.com',
    'https://cdn.jsdelivr.net/npm/chart.js'
];

// Installation — mise en cache des ressources
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            console.log('Cache ouvert');
            return cache.addAll(['/index.html']);
        })
    );
    self.skipWaiting();
});

// Activation — nettoyage des anciens caches
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys.filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            )
        )
    );
    self.clients.claim();
});

// Fetch — réseau en priorité, cache en fallback
self.addEventListener('fetch', event => {
    // Ne pas intercepter les appels API Render
    if (event.request.url.includes('onrender.com') ||
        event.request.url.includes('api.') ||
        event.request.url.includes('groq.com')) {
        return;
    }

    event.respondWith(
        fetch(event.request)
            .then(response => {
                // Mettre en cache la nouvelle réponse
                const responseClone = response.clone();
                caches.open(CACHE_NAME).then(cache => {
                    cache.put(event.request, responseClone);
                });
                return response;
            })
            .catch(() => {
                // Si pas de réseau, utiliser le cache
                return caches.match(event.request);
            })
    );
});
