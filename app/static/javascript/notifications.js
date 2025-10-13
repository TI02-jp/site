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
            if (!seenNotificationIds.has(id)) {
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
  });
})();
