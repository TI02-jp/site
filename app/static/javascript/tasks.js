document.addEventListener('DOMContentLoaded', () => {
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
});
