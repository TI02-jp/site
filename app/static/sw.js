// Service Worker para JP Contábil PWA
const CACHE_NAME = 'jp-contabil-v1';
const CACHE_URLS = [
  '/',
  '/static/styles.css',
  '/static/tasks.css',
  '/static/images/logo-jp-contabil.png',
  '/static/images/icon-192x192.png',
  '/static/images/icon-512x512.png',
  '/static/javascript/mensagens.js',
  '/static/javascript/notifications.js',
  '/static/javascript/modal_cleanup.js',
  '/static/javascript/paste_images.js',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
];

// Instalação do Service Worker
self.addEventListener('install', (event) => {
  console.log('[Service Worker] Instalando...');
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[Service Worker] Cache criado');
      return cache.addAll(CACHE_URLS.map(url => new Request(url, { cache: 'reload' })));
    }).catch((error) => {
      console.error('[Service Worker] Erro ao cachear:', error);
    })
  );
  self.skipWaiting();
});

// Ativação do Service Worker
self.addEventListener('activate', (event) => {
  console.log('[Service Worker] Ativando...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('[Service Worker] Removendo cache antigo:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Estratégia de cache: Network First, fallback to Cache
self.addEventListener('fetch', (event) => {
  // Ignorar requisições não-GET e URLs do Chrome Extension
  if (event.request.method !== 'GET' || event.request.url.startsWith('chrome-extension://')) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Se a resposta for válida, clonar e cachear
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return response;
      })
      .catch(() => {
        // Se falhar, tentar buscar do cache
        return caches.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }
          // Fallback para página offline (opcional)
          if (event.request.mode === 'navigate') {
            return caches.match('/');
          }
        });
      })
  );
});

// Mensagens do cliente
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// Handler para cliques em notificações
self.addEventListener('notificationclick', (event) => {
  console.log('[Service Worker] Notificação clicada:', event.notification.tag);
  console.log('[Service Worker] Ação:', event.action);

  // Se a ação for 'close', apenas fechar
  if (event.action === 'close') {
    event.notification.close();
    return;
  }

  // Para qualquer outra ação ou clique na notificação, abrir a URL
  event.notification.close();

  // Obter a URL da notificação
  const urlToOpen = event.notification.data?.url || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        // Verificar se já existe uma janela aberta
        for (const client of clientList) {
          // Se encontrar uma janela do site, focar nela e navegar
          if (client.url.includes(self.registration.scope) && 'focus' in client) {
            return client.focus().then(() => {
              // Navegar para a URL da notificação
              if ('navigate' in client) {
                return client.navigate(urlToOpen);
              }
            });
          }
        }
        // Se não existe, abrir nova janela
        if (clients.openWindow) {
          return clients.openWindow(urlToOpen);
        }
      })
  );
});

// Handler para eventos push (preparado para futuro uso com push server)
self.addEventListener('push', (event) => {
  console.log('[Service Worker] Push recebido');

  let data = { title: 'Nova notificação', body: 'Você tem uma nova notificação' };

  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body || data.message || 'Você tem uma nova notificação',
    icon: '/static/images/icon-192x192.png',
    badge: '/static/images/icon-192x192.png',
    data: {
      url: data.url || '/',
      dateOfArrival: Date.now(),
      notificationId: data.id
    },
    tag: data.id ? `notification-${data.id}` : 'jp-notification',
    requireInteraction: true, // Mantém visível até o usuário interagir
    vibrate: [200, 100, 200],
    renotify: true, // Renotifica mesmo se já existe uma com a mesma tag
    silent: false, // Som ativado
    timestamp: Date.now(),
    actions: [
      {
        action: 'open',
        title: 'Abrir',
        icon: '/static/images/icon-192x192.png'
      },
      {
        action: 'close',
        title: 'Fechar'
      }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title || 'JP Contábil', options)
  );
});
