/**
 * Attach event listeners to status change buttons
 */
function attachStatusButtonListeners() {
    document.querySelectorAll('.change-status').forEach(btn => {
        // Remove old listeners by cloning
        const newBtn = btn.cloneNode(true);
        btn.parentNode.replaceChild(newBtn, btn);

        newBtn.addEventListener('click', () => {
            const taskId = newBtn.dataset.id;
            const newStatus = newBtn.dataset.status;

            // Get current status from card position
            const taskCard = document.querySelector(`[data-task-id="${taskId}"]`);
            const currentColumn = taskCard ? taskCard.closest('.kanban-list') : null;
            const oldStatus = currentColumn ? currentColumn.dataset.status : null;

            fetch(`/tasks/${taskId}/status`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({ status: newStatus })
            }).then(response => {
                if (response.ok) {
                    return response.json();
                } else {
                    console.error('[Tasks] Status change failed');
                    window.location.reload();
                }
            }).then(data => {
                if (data && data.success && data.task) {
                    // Update immediately for current user
                    console.log('[Tasks] Status change successful, updating UI immediately');
                    handleTaskStatusChanged({
                        id: parseInt(taskId),
                        old_status: oldStatus,
                        new_status: newStatus,
                        task: data.task
                    });
                }
            }).catch(error => {
                console.error('[Tasks] Status change error:', error);
                window.location.reload();
            });
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    window.scrollTo(0, 0);

    // Setup real-time event handlers
    if (window.realtimeClient) {
        setupRealtimeHandlers();
    }

    // Attach status button listeners
    attachStatusButtonListeners();

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
                .then(() => {
                    // Remove task immediately for the user who deleted it
                    console.log('[Tasks] Task deleted successfully, removing from UI');
                    handleTaskDeleted({ id: parseInt(taskId) });
                })
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

/**
 * Setup real-time event handlers for task updates
 */
function setupRealtimeHandlers() {
    const client = window.realtimeClient;

    // Handle task creation
    client.on('task:created', (data) => {
        console.log('[Tasks] New task created:', data);
        handleTaskCreated(data);
    });

    // Handle task status change
    client.on('task:status_changed', (data) => {
        console.log('[Tasks] Task status changed:', data);
        handleTaskStatusChanged(data);
    });

    // Handle task deletion
    client.on('task:deleted', (data) => {
        console.log('[Tasks] Task deleted:', data);
        handleTaskDeleted(data);
    });

    // Handle task update
    client.on('task:updated', (data) => {
        console.log('[Tasks] Task updated:', data);
        handleTaskUpdated(data);
    });
}

/**
 * Handle task created event - add new task card to appropriate column
 */
function handleTaskCreated(taskData) {
    const statusColumn = document.querySelector(`.kanban-list[data-status="${taskData.status}"]`);
    if (!statusColumn) {
        console.warn('[Tasks] Status column not found for:', taskData.status);
        return;
    }

    // Remove "Nenhuma tarefa" message if present
    const emptyMessage = statusColumn.querySelector('.empty');
    if (emptyMessage) {
        emptyMessage.remove();
    }

    // Create task card (simplified version - you may need to expand this)
    const taskCard = createTaskCard(taskData);
    statusColumn.insertBefore(taskCard, statusColumn.firstChild);

    // Add animation
    taskCard.style.animation = 'slideIn 0.3s ease-out';
}

/**
 * Handle task status changed - move task card between columns
 */
function handleTaskStatusChanged(data) {
    const { id, old_status, new_status, task } = data;

    // Find the task card
    const taskCard = document.querySelector(`[data-task-id="${id}"]`);
    if (!taskCard) {
        console.warn('[Tasks] Task card not found:', id);
        // If card not found, might be filtered out - just log it
        return;
    }

    // Find the new status column
    const newStatusColumn = document.querySelector(`.kanban-list[data-status="${new_status}"]`);
    if (!newStatusColumn) {
        console.warn('[Tasks] New status column not found:', new_status);
        return;
    }

    // Remove "Nenhuma tarefa" message if present in new column
    const emptyMessage = newStatusColumn.querySelector('.empty');
    if (emptyMessage) {
        emptyMessage.remove();
    }

    // Add fade-out animation
    taskCard.style.opacity = '0';
    taskCard.style.transition = 'opacity 0.2s ease-out';

    setTimeout(() => {
        // Move card to new column
        taskCard.remove();
        newStatusColumn.insertBefore(taskCard, newStatusColumn.firstChild);

        // Update task card content if needed
        updateTaskCardContent(taskCard, task);

        // Fade in
        taskCard.style.opacity = '1';

        // Check if old column is now empty
        const oldStatusColumn = document.querySelector(`.kanban-list[data-status="${old_status}"]`);
        if (oldStatusColumn && oldStatusColumn.children.length === 0) {
            const emptyLi = document.createElement('li');
            emptyLi.className = 'empty';
            emptyLi.textContent = 'Nenhuma tarefa.';
            oldStatusColumn.appendChild(emptyLi);
        }
    }, 200);
}

/**
 * Handle task deleted - remove task card from DOM
 */
function handleTaskDeleted(data) {
    const { id } = data;

    const taskCard = document.querySelector(`[data-task-id="${id}"]`);
    if (!taskCard) {
        console.warn('[Tasks] Task card not found for deletion:', id);
        return;
    }

    // Add fade-out animation
    taskCard.style.opacity = '0';
    taskCard.style.transition = 'opacity 0.3s ease-out';

    setTimeout(() => {
        const column = taskCard.closest('.kanban-list');
        taskCard.remove();

        // Add "Nenhuma tarefa" message if column is now empty
        if (column && column.children.length === 0) {
            const emptyLi = document.createElement('li');
            emptyLi.className = 'empty';
            emptyLi.textContent = 'Nenhuma tarefa.';
            column.appendChild(emptyLi);
        }
    }, 300);
}

/**
 * Handle task updated - refresh task card content
 */
function handleTaskUpdated(taskData) {
    const taskCard = document.querySelector(`[data-task-id="${taskData.id}"]`);
    if (!taskCard) {
        console.warn('[Tasks] Task card not found for update:', taskData.id);
        return;
    }

    updateTaskCardContent(taskCard, taskData);
}

/**
 * Create a task card element from task data
 * This is a simplified version - expand as needed to match your HTML structure
 */
function createTaskCard(taskData) {
    const li = document.createElement('li');
    li.className = 'task-card';
    li.setAttribute('data-task-id', taskData.id);

    // Simplified HTML - you should match your actual task card structure
    li.innerHTML = `
        <div class="task-header">
            <h4>${escapeHtml(taskData.title)}</h4>
            <span class="task-priority priority-${taskData.priority}">${taskData.priority}</span>
        </div>
        ${taskData.description ? `<p class="task-description">${escapeHtml(taskData.description)}</p>` : ''}
        <div class="task-meta">
            <span class="task-tag">${escapeHtml(taskData.tag_name)}</span>
            ${taskData.due_date ? `<span class="task-due-date">${formatDate(taskData.due_date)}</span>` : ''}
        </div>
    `;

    return li;
}

/**
 * Update task card content with new data
 */
function updateTaskCardContent(taskCard, taskData) {
    // Update title
    const titleElement = taskCard.querySelector('.task-title');
    if (titleElement) {
        titleElement.textContent = taskData.title;
    }

    // Update description
    const descElement = taskCard.querySelector('.task-description');
    if (taskData.description) {
        if (descElement) {
            descElement.textContent = taskData.description;
        }
    } else if (descElement) {
        descElement.remove();
    }

    // Update status class on card
    taskCard.className = `task-card task-status-${taskData.status}`;
    if (taskCard.querySelector('.subtasks')) {
        taskCard.classList.add('has-children');
    }

    // Rebuild action buttons based on new status
    const actionsContainer = taskCard.querySelector('.task-actions');
    if (actionsContainer) {
        rebuildActionButtons(actionsContainer, taskData);
    }

    // Re-attach event listeners to new buttons
    attachStatusButtonListeners();

    // Add visual feedback for update
    taskCard.style.backgroundColor = '#fffacd';
    setTimeout(() => {
        taskCard.style.backgroundColor = '';
        taskCard.style.transition = 'background-color 0.5s ease-out';
    }, 100);
}

/**
 * Rebuild action buttons based on task status
 */
function rebuildActionButtons(actionsContainer, taskData) {
    // Keep existing buttons that don't depend on status
    const viewButton = actionsContainer.querySelector('.view-task');
    const subtaskButton = actionsContainer.querySelector('.subtask');
    const deleteButton = actionsContainer.querySelector('.delete-task');

    // Clear all buttons
    actionsContainer.innerHTML = '';

    // Re-add view button
    if (viewButton) {
        actionsContainer.appendChild(viewButton);
    }

    // Add status-specific buttons
    if (taskData.status === 'pending') {
        const startBtn = document.createElement('button');
        startBtn.className = 'action start change-status';
        startBtn.dataset.id = taskData.id;
        startBtn.dataset.status = 'in_progress';
        startBtn.title = 'Iniciar';
        startBtn.innerHTML = '<i class="bi bi-play-fill"></i>';
        actionsContainer.appendChild(startBtn);
    } else if (taskData.status === 'in_progress') {
        // Update actions container class for compact view
        actionsContainer.classList.add('compact');

        const toPendingBtn = document.createElement('button');
        toPendingBtn.className = 'action to-pending change-status';
        toPendingBtn.dataset.id = taskData.id;
        toPendingBtn.dataset.status = 'pending';
        toPendingBtn.title = 'Mover para pendente';
        toPendingBtn.innerHTML = '<i class="bi bi-arrow-return-left"></i>';
        actionsContainer.appendChild(toPendingBtn);

        const doneBtn = document.createElement('button');
        doneBtn.className = 'action done change-status';
        doneBtn.dataset.id = taskData.id;
        doneBtn.dataset.status = 'done';
        doneBtn.title = 'Concluir';
        doneBtn.innerHTML = '<i class="bi bi-check-circle"></i>';
        actionsContainer.appendChild(doneBtn);
    } else if (taskData.status === 'done') {
        // Check if user is admin (we'll need to pass this info)
        // For now, we won't add reopen button since we don't have user role info
        actionsContainer.classList.remove('compact');
    }

    // Re-add subtask button if it existed
    if (subtaskButton) {
        actionsContainer.appendChild(subtaskButton);
    }

    // Re-add delete button if it existed
    if (deleteButton) {
        actionsContainer.appendChild(deleteButton);
    }
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Format ISO date string to readable format
 */
function formatDate(isoString) {
    const date = new Date(isoString);
    return date.toLocaleDateString('pt-BR');
}
