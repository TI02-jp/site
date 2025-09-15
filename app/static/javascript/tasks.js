document.addEventListener('DOMContentLoaded', () => {
    window.scrollTo(0, 0);

    document.querySelectorAll('.change-status').forEach(btn => {
        btn.addEventListener('click', () => {
            const taskId = btn.dataset.id;
            const status = btn.dataset.status;
            fetch(`/tasks/${taskId}/status`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({ status })
            }).then(() => window.location.reload());
        });
    });

    document.querySelectorAll('.toggle-children').forEach(btn => {
        btn.addEventListener('click', () => {
            const card = btn.closest('.task-card');
            card.classList.toggle('collapsed');
            const icon = btn.querySelector('i');
            icon.classList.toggle('bi-chevron-down');
            icon.classList.toggle('bi-chevron-right');
        });
    });
});
