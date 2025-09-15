// Basic helpers for alerts, modals and tooltips without Bootstrap
function showModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.add('show');
}
function hideModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.remove('show');
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-modal]').forEach(trigger => {
    const target = trigger.getAttribute('data-modal');
    trigger.addEventListener('click', () => showModal(target));
  });

  document.querySelectorAll('[data-close]').forEach(btn => {
    btn.addEventListener('click', () => {
      const modal = btn.closest('.modal');
      if (modal) modal.classList.remove('show');
      const alert = btn.closest('.alert');
      if (alert) alert.remove();
    });
  });

  document.querySelectorAll('.modal').forEach(modal => {
    modal.addEventListener('click', e => {
      if (e.target === modal) {
        modal.classList.remove('show');
      }
    });
  });

  document.querySelectorAll('[data-collapse]').forEach(trigger => {
    const targetSelector = trigger.getAttribute('data-collapse');
    const target = document.querySelector(targetSelector);
    if (!target) return;
    trigger.addEventListener('click', e => {
      e.preventDefault();
      target.classList.toggle('show');
    });
  });
});

window.showModal = showModal;
window.hideModal = hideModal;
