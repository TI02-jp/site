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
      return `${minutes} min atrÃ¡s`;
    }
    if (diff < day) {
      const hours = Math.round(diff / hour);
      return `${hours} h atrÃ¡s`;
    }
    const days = Math.round(diff / day);
    return `${days} d atrÃ¡s`;
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

    // Suporte para notificaÃ§Ãµes do sistema
    const supportsNotifications = 'Notification' in window;
    const supportsServiceWorker = 'serviceWorker' in navigator;
    let notificationPermission = supportsNotifications ? Notification.permission : 'denied';
    const permissionBannerId = 'notificationPermissionBanner';
    let audioContext = null;

    function ensureAudioContext() {
      if (!('AudioContext' in window || 'webkitAudioContext' in window)) {
        return null;
      }
      if (!audioContext) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        audioContext = new Ctx();
      }
      if (audioContext.state === 'suspended') {
        audioContext.resume().catch(() => {});
      }
      return audioContext;
    }

    function playNotificationSound() {
      try {
        const ctx = ensureAudioContext();
        if (ctx) {
          const now = ctx.currentTime;
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'triangle';
          osc.frequency.setValueAtTime(880, now);
          gain.gain.setValueAtTime(0.18, now);
          gain.gain.exponentialRampToValueAtTime(0.001, now + 1.0);
          osc.connect(gain).connect(ctx.destination);
          osc.start(now);
          osc.stop(now + 1.0);
          return;
        }
      } catch (error) {
        console.debug('[Notifications] Som nao reproduzido via WebAudio:', error);
      }

      // Fallback simples usando áudio embutido (pequeno beep)
      try {
        const beep = new Audio('data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=');
        beep.volume = 0.6;
        beep.play().catch(() => {});
      } catch (e) {
        console.debug('[Notifications] Fallback de áudio falhou:', e);
      }
    }

    function renderPermissionBanner(status) {
      if (!supportsNotifications) {
        return;
      }

      let banner = document.getElementById(permissionBannerId);
      if (status === 'granted') {
        if (banner) {
          banner.remove();
        }
        return;
      }

      if (!banner) {
        banner = document.createElement('div');
        banner.id = permissionBannerId;
        banner.className = 'alert alert-warning d-flex align-items-center gap-3 mb-0 shadow-sm';
        banner.style.position = 'sticky';
        banner.style.top = '0';
        banner.style.zIndex = '1100';
        banner.style.borderRadius = '0';
        banner.style.borderBottom = '1px solid rgba(0,0,0,0.05)';
        const anchor = document.querySelector('.wrapper') || document.body.firstChild;
        if (anchor && anchor.parentNode) {
          anchor.parentNode.insertBefore(banner, anchor);
        } else {
          document.body.prepend(banner);
        }
      }

      if (status === 'default') {
        banner.innerHTML = `
          <div class="d-flex align-items-center gap-2 flex-wrap">
            <i class="bi bi-bell fs-5 text-warning"></i>
            <div class="flex-grow-1">As notificacoes estao bloqueadas no navegador. Clique no cadeado da barra de endereco e permita notificacoes para o JP.</div>
            <button type="button" class="btn btn-sm btn-primary" id="permissionBannerAllow">Ativar notificacoes</button>
          </div>
        `;
        const allowBtn = banner.querySelector('#permissionBannerAllow');
        if (allowBtn) {
          allowBtn.addEventListener('click', () => {
            requestNotificationPermission();
          });
        }
      } else {
        banner.innerHTML = `
          <div class="d-flex align-items-center gap-2 flex-wrap">
            <i class="bi bi-bell-slash fs-5 text-danger"></i>
            <div class="flex-grow-1">As notificacoes estao bloqueadas no navegador. Clique no cadeado da barra de endereco e permita notificacoes para o JP.</div>
          </div>
        `;
      }
    }
    renderPermissionBanner(notificationPermission);

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

    // FunÃ§Ã£o para obter chave pÃºblica VAPID do servidor
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

    // FunÃ§Ã£o para converter chave VAPID de base64 para Uint8Array
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

    // FunÃ§Ã£o para subscrever ao Push usando Service Worker
    async function subscribeToPush() {
      if (!supportsServiceWorker || !supportsNotifications) {
        console.log('[Notifications] Push nÃ£o suportado neste navegador');
        renderPermissionBanner(notificationPermission);
        return false;
      }

      try {
        const registration = await navigator.serviceWorker.ready;

        // Obter chave pÃºblica VAPID
        const vapidPublicKey = await getVapidPublicKey();
        if (!vapidPublicKey) {
          console.error('[Notifications] Chave VAPID nÃ£o disponÃ­vel');
          return false;
        }

        const applicationServerKey = urlBase64ToUint8Array(vapidPublicKey);

        // Verificar se jÃ¡ existe uma subscriÃ§Ã£o
        let subscription = await registration.pushManager.getSubscription();

        if (!subscription) {
          // Criar nova subscriÃ§Ã£o
          subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: applicationServerKey
          });
          console.log('[Notifications] Nova subscriÃ§Ã£o Push criada');
        } else {
          console.log('[Notifications] SubscriÃ§Ã£o Push jÃ¡ existe');
        }

        // Enviar subscriÃ§Ã£o para o servidor
        const response = await fetch('/notifications/subscribe', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify(subscription.toJSON())
        });

        if (response.ok) {
          console.log('[Notifications] SubscriÃ§Ã£o Push registrada no servidor');
          return true;
        } else {
          console.error('[Notifications] Erro ao registrar subscriÃ§Ã£o no servidor');
          return false;
        }
      } catch (error) {
        console.error('[Notifications] Erro ao subscrever ao Push:', error);
        return false;
      }
    }

    // FunÃ§Ã£o para solicitar permissÃ£o de notificaÃ§Ãµes do sistema
    async function requestNotificationPermission() {
      if (!supportsNotifications) {
        console.log('[Notifications] NotificaÃ§Ãµes do sistema nÃ£o suportadas');
        return false;
      }

      if (notificationPermission === 'granted') {
        // Se jÃ¡ tem permissÃ£o, subscrever ao Push
        subscribeToPush();
        return true;
      }

      if (notificationPermission === 'denied') {
        console.log('[Notifications] PermissÃ£o negada pelo usuÃ¡rio');
        renderPermissionBanner(notificationPermission);
        return false;
      }

      try {
        const permission = await Notification.requestPermission();
        notificationPermission = permission;
        console.log('[Notifications] Permissão:', permission);
        renderPermissionBanner(notificationPermission);

        if (permission === 'granted') {
          // Subscrever ao Push apÃ³s obter permissÃ£o
          await subscribeToPush();
        }

        return permission === 'granted';
      } catch (error) {
        console.error('[Notifications] Erro ao solicitar permissÃ£o:', error);
        return false;
      }
    }

    // FunÃ§Ã£o para criar notificaÃ§Ã£o do sistema usando Service Worker
    async function showSystemNotification(item) {
      if (!item) {
        return;
      }

      // Verificar se temos permissÃ£o
      if (notificationPermission !== 'granted') {
        const granted = await requestNotificationPermission();
        if (!granted) {
          console.log('[Notifications] Usando notificaÃ§Ã£o in-app (sem permissÃ£o do sistema)');
          return false;
        }
      }

      const title = 'JP ContÃ¡bil';
      const options = {
        body: item.message || 'Nova notificaÃ§Ã£o',
        icon: '/static/images/icon-192x192.png',
        badge: '/static/images/icon-192x192.png',
        tag: item.id ? `notification-${item.id}` : 'jp-notification',
        data: {
          url: item.url || '/',
          notificationId: item.id,
          dateOfArrival: Date.now()
        },
        requireInteraction: true, // MantÃ©m a notificaÃ§Ã£o visÃ­vel atÃ© o usuÃ¡rio interagir
        silent: false, // Som ativado
        vibrate: [200, 100, 200],
        renotify: true, // Renotifica mesmo se jÃ¡ existe uma com a mesma tag
        timestamp: Date.now()
      };

      try {
        // Tentar usar Service Worker se disponÃ­vel
        if (supportsServiceWorker && navigator.serviceWorker.ready) {
          const registration = await navigator.serviceWorker.ready;
          await registration.showNotification(title, options);
          playNotificationSound();
          console.log('[Notifications] Notificacao do sistema mostrada via SW');
          return true;
        } else {
          // Fallback para Notification API direta
          const notification = new Notification(title, options);
          playNotificationSound();

          notification.onclick = function(event) {
            event.preventDefault();
            window.focus();
            if (item.url) {
              window.location.href = item.url;
            }
            notification.close();
          };

          console.log('[Notifications] NotificaÃ§Ã£o do sistema mostrada diretamente');
          return true;
        }
      } catch (error) {
        console.error('[Notifications] Erro ao mostrar notificaÃ§Ã£o do sistema:', error);
        return false;
      }
    }

    // Solicitar permissÃ£o automaticamente ao carregar a pÃ¡gina
    setTimeout(() => {
      if (supportsNotifications && notificationPermission === 'default') {
        console.log('[Notifications] Solicitando permissÃ£o para notificaÃ§Ãµes do sistema...');
        requestNotificationPermission().then(granted => {
          if (granted) {
            console.log('[Notifications] PermissÃ£o concedida! NotificaÃ§Ãµes do Windows habilitadas.');
          } else {
            console.log('[Notifications] PermissÃ£o negada. As notificaÃ§Ãµes ficarÃ£o apenas no navegador.');
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

      // SEMPRE criar notificaÃ§Ã£o do sistema (desktop) se tivermos permissÃ£o
      // Isso garante que apareÃ§a como pop-up nativo do Windows
      if (notificationPermission === 'granted') {
        showSystemNotification(item).then(success => {
          if (success) {
            console.log('[Notifications] NotificaÃ§Ã£o do Windows exibida com sucesso!');
          }
        }).catch(error => {
          console.error('[Notifications] Falha ao criar notificaÃ§Ã£o do sistema:', error);
        });
      } else {
        // Se nÃ£o temos permissÃ£o, tentar solicitar novamente
        console.log('[Notifications] Tentando solicitar permissÃ£o novamente...');
        requestNotificationPermission().then(granted => {
          if (granted) {
            showSystemNotification(item);
          }
        });
      }

      if (!toastContainer) {
        return;
      }
      const message = escapeHtml(item.message || 'Nova notificaÃ§Ã£o');
      const time = formatRelativeTime(item.created_at);
      const actionLabel = escapeHtml(item.action_label || 'Abrir');
      const toast = document.createElement('div');
      toast.className = 'notification-toast';
      toast.innerHTML = `
        <span class="notification-toast__title">${message}</span>
        <div class="notification-toast__meta">
          <span>${time || 'agora'}</span>
          <button type="button" class="btn btn-link p-0 notification-toast__dismiss" aria-label="Fechar notificaÃ§Ã£o">
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
      playNotificationSound();

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

      // Mantém o toast até interação explícita (abrir ou fechar)
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
            throw new Error('Erro ao carregar notificaÃ§Ãµes');
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
            console.warn('Falha ao interpretar evento de notificaÃ§Ã£o.', error);
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

    // Expor funÃ§Ã£o globalmente para poder ser chamada pela UI
    window.jpNotifications = {
      requestPermission: requestNotificationPermission,
      hasPermission: () => notificationPermission === 'granted',
      isSupported: () => supportsNotifications,
      getPermissionStatus: () => notificationPermission
    };
  });
})();





