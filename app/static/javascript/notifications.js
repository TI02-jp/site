(function () {
  function escapeHtml(text) {
    if (!text) {
      return '';
    }
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatRelativeTime(isoString) {
    if (!isoString) {
      return '';
    }
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) {
      return '';
    }
    const now = Date.now();
    let diff = now - date.getTime();
    if (diff < 0) {
      diff = 0;
    }
    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;
    if (diff < minute) {
      return 'agora';
    }
    if (diff < hour) {
      const minutes = Math.round(diff / minute);
      return `${minutes} min atrás`;
    }
    if (diff < day) {
      const hours = Math.round(diff / hour);
      return `${hours} h atrás`;
    }
    const days = Math.round(diff / day);
    return `${days} d atrás`;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const wrapper = document.getElementById('notificationWrapper');
    const button = document.getElementById('notificationButton');
    const dropdown = document.getElementById('notificationDropdown');
    const countBadge = document.getElementById('notificationCount');
    const listEl = document.getElementById('notificationList');
    const emptyEl = document.getElementById('notificationEmpty');
    const markAllBtn = document.getElementById('notificationMarkAll');
    const navBadge = document.querySelector('[data-notifications-badge]');
    const toastContainer = document.getElementById('notificationToastContainer');
    const supportsDropdown =
      wrapper && button && dropdown && listEl && emptyEl;

    if (!supportsDropdown && !navBadge) {
      return;
    }

    const supportsSSE = 'EventSource' in window;
    const csrfToken = window.csrfToken || '';
    let isOpen = false;
    let isFetching = false;
    const knownNotificationIds = new Set();
    let initialFetchComplete = false;
    let lastKnownNotificationId = 0;

    const displayedNotificationIds = new Set();

    // Suporte para notificações do sistema
    const supportsNotifications = 'Notification' in window;
    const supportsServiceWorker = 'serviceWorker' in navigator;
    let notificationPermission = supportsNotifications ? Notification.permission : 'denied';

    function updateLastKnownId(id) {
      if (!id) {
        return;
      }
      const numeric = Number(id);
      if (!Number.isNaN(numeric)) {
        lastKnownNotificationId = Math.max(lastKnownNotificationId, numeric);
      }
    }

    function setBadge(count) {
      const hasUnread = Boolean(count && count > 0);
      if (countBadge) {
        if (hasUnread) {
          countBadge.textContent = count;
          countBadge.hidden = false;
        } else {
          countBadge.hidden = true;
        }
      }
      if (button) {
        button.classList.toggle('has-unread', hasUnread);
      }
      if (navBadge) {
        navBadge.textContent = String(count || 0);
        if (hasUnread) {
          navBadge.classList.remove('d-none');
        } else {
          navBadge.classList.add('d-none');
        }
      }
    }

    function renderNotifications(items) {
      if (!listEl || !emptyEl) {
        return;
      }
      listEl.innerHTML = '';
      if (!Array.isArray(items) || items.length === 0) {
        emptyEl.style.display = 'block';
        listEl.style.display = 'none';
        if (markAllBtn) {
          markAllBtn.disabled = true;
          markAllBtn.style.visibility = 'hidden';
        }
        return;
      }
      emptyEl.style.display = 'none';
      listEl.style.display = 'block';
      const hasUnread = items.some((item) => !item.is_read);
      if (markAllBtn) {
        markAllBtn.disabled = !hasUnread;
        markAllBtn.style.visibility = hasUnread ? 'visible' : 'hidden';
      }
      items.forEach((item) => {
        const li = document.createElement('li');
        li.className = `notification-item ${item.is_read ? 'read' : 'unread'}`;
        const link = document.createElement('a');
        link.href = item.url || '#';
        link.dataset.id = String(item.id);
        if (item.type) {
          link.dataset.type = String(item.type);
        }
        link.dataset.url = item.url || '';
        link.innerHTML = `
          <span class="notification-title">${escapeHtml(item.message)}</span>
          <span class="notification-time">${formatRelativeTime(item.created_at)}</span>
        `;
        li.appendChild(link);
        listEl.appendChild(li);
      });
    }

    function hideToast(toast) {
      if (!toast) {
        return;
      }
      toast.classList.add('hide');
      toast.addEventListener(
        'transitionend',
        () => {
          toast.remove();
        },
        { once: true }
      );
    }

    function rememberNotification(id) {
      if (!id) {
        return;
      }
      displayedNotificationIds.add(id);
    }

    // Função para obter chave pública VAPID do servidor
    async function getVapidPublicKey() {
      try {
        const response = await fetch('/notifications/vapid-public-key');
        const data = await response.json();
        return data.publicKey;
      } catch (error) {
        console.error('[Notifications] Erro ao obter chave VAPID:', error);
        return null;
      }
    }

    // Função para converter chave VAPID de base64 para Uint8Array
    function urlBase64ToUint8Array(base64String) {
      const padding = '='.repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding)
        .replace(/\-/g, '+')
        .replace(/_/g, '/');

      const rawData = window.atob(base64);
      const outputArray = new Uint8Array(rawData.length);

      for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
      }
      return outputArray;
    }

    // Função para subscrever ao Push usando Service Worker
    async function subscribeToPush() {
      if (!supportsServiceWorker || !supportsNotifications) {
        console.log('[Notifications] Push não suportado neste navegador');
        return false;
      }

      try {
        const registration = await navigator.serviceWorker.ready;

        // Obter chave pública VAPID
        const vapidPublicKey = await getVapidPublicKey();
        if (!vapidPublicKey) {
          console.error('[Notifications] Chave VAPID não disponível');
          return false;
        }

        const applicationServerKey = urlBase64ToUint8Array(vapidPublicKey);

        // Verificar se já existe uma subscrição
        let subscription = await registration.pushManager.getSubscription();

        if (!subscription) {
          // Criar nova subscrição
          subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: applicationServerKey
          });
          console.log('[Notifications] Nova subscrição Push criada');
        } else {
          console.log('[Notifications] Subscrição Push já existe');
        }

        // Enviar subscrição para o servidor
        const response = await fetch('/notifications/subscribe', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify(subscription.toJSON())
        });

        if (response.ok) {
          console.log('[Notifications] Subscrição Push registrada no servidor');
          return true;
        } else {
          console.error('[Notifications] Erro ao registrar subscrição no servidor');
          return false;
        }
      } catch (error) {
        console.error('[Notifications] Erro ao subscrever ao Push:', error);
        return false;
      }
    }

    // Função para solicitar permissão de notificações do sistema
    async function requestNotificationPermission() {
      if (!supportsNotifications) {
        console.log('[Notifications] Notificações do sistema não suportadas');
        return false;
      }

      if (notificationPermission === 'granted') {
        // Se já tem permissão, subscrever ao Push
        subscribeToPush();
        return true;
      }

      if (notificationPermission === 'denied') {
        console.log('[Notifications] Permissão negada pelo usuário');
        return false;
      }

      try {
        const permission = await Notification.requestPermission();
        notificationPermission = permission;
        console.log('[Notifications] Permissão:', permission);

        if (permission === 'granted') {
          // Subscrever ao Push após obter permissão
          await subscribeToPush();
        }

        return permission === 'granted';
      } catch (error) {
        console.error('[Notifications] Erro ao solicitar permissão:', error);
        return false;
      }
    }

    // Função para criar notificação do sistema usando Service Worker
    async function showSystemNotification(item) {
      if (!item) {
        return;
      }

      // Verificar se temos permissão
      if (notificationPermission !== 'granted') {
        const granted = await requestNotificationPermission();
        if (!granted) {
          console.log('[Notifications] Usando notificação in-app (sem permissão do sistema)');
          return false;
        }
      }

      const title = 'JP Contábil';
      const options = {
        body: item.message || 'Nova notificação',
        icon: '/static/images/icon-192x192.png',
        badge: '/static/images/icon-192x192.png',
        tag: item.id ? `notification-${item.id}` : 'jp-notification',
        data: {
          url: item.url || '/',
          notificationId: item.id,
          dateOfArrival: Date.now()
        },
        requireInteraction: true, // Mantém a notificação visível até o usuário interagir
        silent: false, // Som ativado
        vibrate: [200, 100, 200],
        renotify: true, // Renotifica mesmo se já existe uma com a mesma tag
        timestamp: Date.now()
      };

      try {
        // Tentar usar Service Worker se disponível
        if (supportsServiceWorker && navigator.serviceWorker.ready) {
          const registration = await navigator.serviceWorker.ready;
          await registration.showNotification(title, options);
          console.log('[Notifications] Notificação do sistema mostrada via SW');
          return true;
        } else {
          // Fallback para Notification API direta
          const notification = new Notification(title, options);

          notification.onclick = function(event) {
            event.preventDefault();
            window.focus();
            if (item.url) {
              window.location.href = item.url;
            }
            notification.close();
          };

          console.log('[Notifications] Notificação do sistema mostrada diretamente');
          return true;
        }
      } catch (error) {
        console.error('[Notifications] Erro ao mostrar notificação do sistema:', error);
        return false;
      }
    }

    // Solicitar permissão automaticamente ao carregar a página
    setTimeout(() => {
      if (supportsNotifications && notificationPermission === 'default') {
        console.log('[Notifications] Solicitando permissão para notificações do sistema...');
        requestNotificationPermission().then(granted => {
          if (granted) {
            console.log('[Notifications] Permissão concedida! Notificações do Windows habilitadas.');
          } else {
            console.log('[Notifications] Permissão negada. As notificações ficarão apenas no navegador.');
          }
        });
      }
    }, 2000);

    function showNotificationToast(item) {
      if (!item) {
        return;
      }
      const id = item.id != null ? String(item.id) : null;
      if (id) {
        rememberNotification(id);
        updateLastKnownId(id);
        knownNotificationIds.add(id);
      }

      // SEMPRE criar notificação do sistema (desktop) se tivermos permissão
      // Isso garante que apareça como pop-up nativo do Windows
      if (notificationPermission === 'granted') {
        showSystemNotification(item).then(success => {
          if (success) {
            console.log('[Notifications] Notificação do Windows exibida com sucesso!');
          }
        }).catch(error => {
          console.error('[Notifications] Falha ao criar notificação do sistema:', error);
        });
      } else {
        // Se não temos permissão, tentar solicitar novamente
        console.log('[Notifications] Tentando solicitar permissão novamente...');
        requestNotificationPermission().then(granted => {
          if (granted) {
            showSystemNotification(item);
          }
        });
      }

      if (!toastContainer) {
        return;
      }
      const message = escapeHtml(item.message || 'Nova notificação');
      const time = formatRelativeTime(item.created_at);
      const actionLabel = escapeHtml(item.action_label || 'Abrir');
      const toast = document.createElement('div');
      toast.className = 'notification-toast';
      toast.innerHTML = `
        <span class="notification-toast__title">${message}</span>
        <div class="notification-toast__meta">
          <span>${time || 'agora'}</span>
          <button type="button" class="btn btn-link p-0 notification-toast__dismiss" aria-label="Fechar notificação">
            <i class="bi bi-x-lg"></i>
          </button>
        </div>
        ${
          item.url
            ? `<div class="notification-toast__actions">
                <button type="button" class="btn btn-primary btn-sm notification-toast__open">${actionLabel}</button>
              </div>`
            : ''
        }
      `;
      toastContainer.appendChild(toast);
      requestAnimationFrame(() => {
        toast.classList.add('show');
      });

      const handleOpen = () => {
        if (!item.url) {
          hideToast(toast);
          return;
        }
        markNotificationRead(item.id)
          .catch(() => {})
          .finally(() => {
            hideToast(toast);
            window.location.href = item.url;
          });
      };

      const dismissBtn = toast.querySelector('.notification-toast__dismiss');
      if (dismissBtn) {
        dismissBtn.addEventListener('click', (event) => {
          event.stopPropagation();
          hideToast(toast);
        });
      }

      const openBtn = toast.querySelector('.notification-toast__open');
      if (openBtn) {
        openBtn.addEventListener('click', (event) => {
          event.stopPropagation();
          handleOpen();
        });
        toast.addEventListener('click', () => {
          handleOpen();
        });
      }

      setTimeout(() => {
        hideToast(toast);
      }, 6500);
    }

    function fetchNotifications(options = {}) {
      const suppressToasts = Boolean(options.suppressToasts);
      if (isFetching) {
        return Promise.resolve();
      }
      if (!window.navigator.onLine) {
        return Promise.resolve();
      }
      isFetching = true;
      return fetch('/notifications', {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
        },
      })
        .then((response) => {
          if (!response.ok) {
            throw new Error('Erro ao carregar notificações');
          }
          return response.json();
        })
        .then((data) => {
          const notifications = Array.isArray(data.notifications)
            ? data.notifications
            : [];
          const unread = data.unread || 0;

          notifications.forEach((item) => {
            if (!item) {
              return;
            }
            const id = item.id != null ? String(item.id) : null;
            if (!id) {
              return;
            }
            updateLastKnownId(id);
            knownNotificationIds.add(id);
          });

          if (!initialFetchComplete) {
            notifications.forEach((item) => {
              const id = item && item.id != null ? String(item.id) : null;
              if (id) {
                rememberNotification(id);
              }
            });
            initialFetchComplete = true;
          } else if (!suppressToasts) {
            notifications.forEach((item) => {
              if (!item || item.is_read) {
                return;
              }
              const id = item.id != null ? String(item.id) : null;
              if (!id || displayedNotificationIds.has(id)) {
                return;
              }
              showNotificationToast(item);
            });
          }

          setBadge(unread);
          renderNotifications(notifications);
        })
        .catch(() => {
          // Ignore network errors silently
        })
        .finally(() => {
          isFetching = false;
        });
    }

    function markNotificationRead(id) {
      return fetch(`/notifications/${id}/read`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken,
        },
      });
    }

    function markAllNotificationsRead() {
      return fetch('/notifications/read-all', {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken,
        },
      });
    }

    function openDropdown() {
      if (!supportsDropdown) {
        return;
      }
      if (isOpen) {
        return;
      }
      dropdown.classList.add('show');
      button.setAttribute('aria-expanded', 'true');
      button.classList.add('active');
      isOpen = true;
    }

    function closeDropdown() {
      if (!supportsDropdown) {
        return;
      }
      if (!isOpen) {
        return;
      }
      dropdown.classList.remove('show');
      button.setAttribute('aria-expanded', 'false');
      button.classList.remove('active');
      isOpen = false;
    }

    if (supportsDropdown) {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        if (isOpen) {
          closeDropdown();
        } else {
          openDropdown();
          fetchNotifications();
        }
      });

      document.addEventListener('click', (event) => {
        if (wrapper && !wrapper.contains(event.target)) {
          closeDropdown();
        }
      });

      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          closeDropdown();
        }
      });

      listEl.addEventListener('click', (event) => {
        const link = event.target.closest('a[data-id]');
        if (!link) {
          return;
        }
        event.preventDefault();
        const notificationId = link.dataset.id;
        const targetUrl = link.dataset.url;
        if (!notificationId) {
          return;
        }
        markNotificationRead(notificationId)
          .catch(() => {})
          .finally(() => {
            closeDropdown();
            fetchNotifications();
            if (targetUrl) {
              window.location.href = targetUrl;
            }
          });
      });

      if (markAllBtn) {
        markAllBtn.addEventListener('click', (event) => {
          event.preventDefault();
          if (markAllBtn.disabled) {
            return;
          }
          markAllNotificationsRead()
            .catch(() => {})
            .finally(() => {
              fetchNotifications();
            });
        });
      }
    }

    const POLL_INTERVAL_MS = 2000;
    let pollTimer = null;

    function stopPolling() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function startPolling() {
      stopPolling();
      pollTimer = setInterval(() => {
        if (!document.hidden) {
          fetchNotifications();
        }
      }, POLL_INTERVAL_MS);
    }

    function connectEventStream() {
      if (!supportsSSE) {
        startPolling();
        return;
      }

      let source;

      const establishConnection = () => {
        const url =
          lastKnownNotificationId > 0
            ? `/notifications/stream?since=${encodeURIComponent(lastKnownNotificationId)}`
            : '/notifications/stream';
        source = new EventSource(url, { withCredentials: true });

        source.addEventListener('message', (event) => {
          if (!event.data) {
            return;
          }
          let payload;
          try {
            payload = JSON.parse(event.data);
          } catch (error) {
            console.warn('Falha ao interpretar evento de notificação.', error);
            return;
          }

          const incoming = Array.isArray(payload.notifications)
            ? payload.notifications
            : [];
          incoming.forEach((item) => {
            if (!item) {
              return;
            }
            const id = item.id != null ? String(item.id) : null;
            if (!id) {
              return;
            }
            updateLastKnownId(id);
            if (!displayedNotificationIds.has(id)) {
              showNotificationToast(item);
            }
            knownNotificationIds.add(id);
          });

          if (typeof payload.unread === 'number') {
            setBadge(payload.unread);
          }

          fetchNotifications({ suppressToasts: true });
        });

        source.addEventListener('error', () => {
          if (source) {
            source.close();
          }
          setTimeout(establishConnection, 5000);
        });
      };

      establishConnection();
    }

    fetchNotifications()
      .catch(() => {})
      .finally(() => {
        connectEventStream();
        startPolling();
      });

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        fetchNotifications({ suppressToasts: true }).finally(() => {
          startPolling();
        });
      } else {
        stopPolling();
      }
    });

    // Expor função globalmente para poder ser chamada pela UI
    window.jpNotifications = {
      requestPermission: requestNotificationPermission,
      hasPermission: () => notificationPermission === 'granted',
      isSupported: () => supportsNotifications,
      getPermissionStatus: () => notificationPermission
    };
  });
})();
