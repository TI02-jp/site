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
    if (!wrapper || !button || !dropdown || !listEl || !emptyEl) {
      return;
    }

    const csrfToken = window.csrfToken || '';
    let isOpen = false;
    let isFetching = false;

    function setBadge(count) {
      if (!countBadge) {
        return;
      }
      if (count && count > 0) {
        countBadge.textContent = count;
        countBadge.hidden = false;
      } else {
        countBadge.hidden = true;
      }
    }

    function renderNotifications(items) {
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
      if (isOpen) {
        return;
      }
      dropdown.classList.add('show');
      button.setAttribute('aria-expanded', 'true');
      isOpen = true;
    }

    function closeDropdown() {
      if (!isOpen) {
        return;
      }
      dropdown.classList.remove('show');
      button.setAttribute('aria-expanded', 'false');
      isOpen = false;
    }

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
      if (!wrapper.contains(event.target)) {
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

    fetchNotifications();
    setInterval(fetchNotifications, 60000);
  });
})();
