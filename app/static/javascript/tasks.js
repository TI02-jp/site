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

    document.querySelectorAll('.delete-task').forEach(btn => {
        btn.addEventListener('click', () => {
            const taskId = btn.dataset.id;
            const title = btn.dataset.title || '';
            const confirmationMessage = title
                ? `Deseja realmente excluir a tarefa "${title}"? Esta ação não pode ser desfeita.`
                : 'Deseja realmente excluir esta tarefa? Esta ação não pode ser desfeita.';
            if (!window.confirm(confirmationMessage)) {
                return;
            }
            fetch(`/tasks/${taskId}/delete`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
            })
                .then(response => {
                    if (!response.ok) {
                        throw new Error('Falha ao excluir a tarefa.');
                    }
                    return response.json();
                })
                .then(() => window.location.reload())
                .catch(error => {
                    console.error(error);
                    window.alert('Não foi possível excluir a tarefa. Tente novamente mais tarde.');
                });
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

    const detailButtons = document.querySelectorAll('.view-task');
    if (detailButtons.length) {
        const closeAllDetails = (currentCard) => {
            document.querySelectorAll('.task-card.show-details').forEach(openCard => {
                if (openCard !== currentCard) {
                    openCard.classList.remove('show-details');
                    const openButton = openCard.querySelector('.view-task[aria-expanded="true"]');
                    if (openButton) {
                        openButton.setAttribute('aria-expanded', 'false');
                    }
                }
            });
        };

        detailButtons.forEach(btn => {
            btn.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                const card = btn.closest('.task-card');
                if (!card) {
                    return;
                }
                const isOpen = card.classList.contains('show-details');
                closeAllDetails(card);
                if (isOpen) {
                    card.classList.remove('show-details');
                    btn.setAttribute('aria-expanded', 'false');
                } else {
                    card.classList.add('show-details');
                    btn.setAttribute('aria-expanded', 'true');
                }
            });
        });

        document.addEventListener('keyup', event => {
            if (event.key === 'Escape') {
                document.querySelectorAll('.task-card.show-details').forEach(card => {
                    card.classList.remove('show-details');
                    const button = card.querySelector('.view-task');
                    if (button) {
                        button.setAttribute('aria-expanded', 'false');
                    }
                });
            }
        });

        document.addEventListener('click', event => {
            if (!event.target.closest('.task-card')) {
                document.querySelectorAll('.task-card.show-details').forEach(card => {
                    card.classList.remove('show-details');
                    const button = card.querySelector('.view-task');
                    if (button) {
                        button.setAttribute('aria-expanded', 'false');
                    }
                });
            }
        });
    }

    const tagSelect = document.getElementById('tag_id');
    const userSelect = document.getElementById('assigned_to');
    if (tagSelect && userSelect) {
        const loadUsers = (tagId) => {
            fetch(`/tasks/users/${tagId}`)
                .then(res => res.json())
                .then(users => {
                    userSelect.innerHTML = '<option value="0">Sem responsável</option>';
                    users.forEach(u => {
                        const opt = document.createElement('option');
                        opt.value = u.id;
                        opt.textContent = u.name;
                        userSelect.appendChild(opt);
                    });
                });
        };
        if (tagSelect.value) {
            loadUsers(tagSelect.value);
        }
        tagSelect.addEventListener('change', () => loadUsers(tagSelect.value));
    }

    const assignedToMeCheckbox = document.getElementById('assigned-to-me-checkbox');
    if (assignedToMeCheckbox) {
        assignedToMeCheckbox.addEventListener('change', () => {
            const form = assignedToMeCheckbox.closest('form');
            if (form) {
                form.submit();
            }
        });
    }

    const assignedByMeCheckbox = document.getElementById('assigned-by-me-checkbox');
    if (assignedByMeCheckbox) {
        assignedByMeCheckbox.addEventListener('change', () => {
            const form = assignedByMeCheckbox.closest('form');
            if (form) {
                form.submit();
            }
        });
    }
});
