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
    const supportsDropdown =
      wrapper && button && dropdown && listEl && emptyEl;

    if (!supportsDropdown && !navBadge) {
      return;
    }

    const csrfToken = window.csrfToken || '';
    let isOpen = false;
    let isFetching = false;

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
        link.dataset.url = item.url || '';
        link.innerHTML = `
          <span class="notification-title">${escapeHtml(item.message)}</span>
          <span class="notification-time">${formatRelativeTime(item.created_at)}</span>
        `;
        li.appendChild(link);
        listEl.appendChild(li);
      });
    }

    function fetchNotifications() {
      if (isFetching) {
        return Promise.resolve();
      }
      isFetching = true;
      return fetch('/notifications', { cache: 'no-store' })
        .then((response) => {
          if (!response.ok) {
            throw new Error('Erro ao carregar notificações');
          }
          return response.json();
        })
        .then((data) => {
          const notifications = data.notifications || [];
          const unread = data.unread || 0;
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

    fetchNotifications();
    setInterval(fetchNotifications, 60000);
  });
})();
