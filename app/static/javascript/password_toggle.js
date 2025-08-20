document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    const targetSelector = button.getAttribute('data-password-toggle');
    const input = document.querySelector(targetSelector);
    if (!input) return;
    button.addEventListener('click', () => {
      const type = input.getAttribute('type') === 'password' ? 'text' : 'password';
      input.setAttribute('type', type);
      const icon = button.querySelector('i');
      if (icon) {
        if (icon.classList.contains('fa-eye') || icon.classList.contains('fa-eye-slash')) {
          icon.classList.toggle('fa-eye');
          icon.classList.toggle('fa-eye-slash');
        }
        if (icon.classList.contains('bi-eye') || icon.classList.contains('bi-eye-slash')) {
          icon.classList.toggle('bi-eye');
          icon.classList.toggle('bi-eye-slash');
        }
      }
    });
  });
});
