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

const TaskResponses = (() => {
    const BUTTON_SELECTOR = '.open-task-responses';
    const ACTIVE_STATUSES = new Set(['in_progress', 'done']);
    const state = {
        drawer: null,
        panel: null,
        backdrop: null,
        form: null,
        textarea: null,
        messages: null,
        summary: null,
        subtitle: null,
        hint: null,
        currentTaskId: null,
        button: null,
        meta: null,
        loading: false,
        currentUserId: 0,
        csrfToken: null,
    };

    function init(options = {}) {
        state.csrfToken = options.csrfToken || state.csrfToken;
        state.currentUserId = options.currentUserId || state.currentUserId;

        refreshButtons();

        state.drawer = document.getElementById('task-response-drawer');
        if (!state.drawer) {
            return;
        }

        state.panel = state.drawer.querySelector('.drawer-panel');
        state.backdrop = state.drawer.querySelector('.drawer-backdrop');
        state.form = state.drawer.querySelector('[data-response-form]');
        state.textarea = state.drawer.querySelector('.response-input');
        state.messages = state.drawer.querySelector('[data-messages-container]');
        state.summary = state.drawer.querySelector('[data-response-summary]');
        state.subtitle = state.drawer.querySelector('[data-response-status]');
        state.hint = state.drawer.querySelector('[data-response-hint]');

        document.addEventListener('click', onGlobalClick);
        document.addEventListener('keydown', onGlobalKeydown);

        if (state.form) {
            state.form.addEventListener('submit', handleSubmit);
        }

        if (state.textarea) {
            state.textarea.addEventListener('keydown', (event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !state.textarea.disabled) {
                    event.preventDefault();
                    if (state.form) {
                        state.form.requestSubmit();
                    }
                }
            });
        }

        state.drawer.querySelectorAll('[data-close-responses]').forEach((btn) => {
            btn.addEventListener('click', (event) => {
                event.preventDefault();
                closeDrawer();
            });
        });

        if (state.backdrop) {
            state.backdrop.addEventListener('click', (event) => {
                event.preventDefault();
                closeDrawer();
            });
        }
    }

    function refreshButtons(scope = document) {
        scope.querySelectorAll(BUTTON_SELECTOR).forEach((button) => {
            if (!button.dataset.unread) {
                button.dataset.unread = '0';
            }
            if (!button.dataset.total) {
                button.dataset.total = '0';
            }
            applyButtonVisual(button);
        });
    }

    function applyButtonVisual(button) {
        const unread = parseInt(button.dataset.unread || '0', 10);
        const total = parseInt(button.dataset.total || '0', 10);

        let badge = button.querySelector('.badge');
        if ((unread > 0 || total > 0) && !badge) {
            badge = document.createElement('span');
            badge.className = 'badge';
            button.appendChild(badge);
        }

        if (badge) {
            if (unread > 0) {
                badge.textContent = unread > 99 ? '99+' : String(unread);
                badge.classList.add('unread');
            } else if (total > 0) {
                badge.textContent = total > 99 ? '99+' : String(total);
                badge.classList.remove('unread');
            } else {
                badge.remove();
            }
        }

        const icon = button.querySelector('i');
        if (icon) {
            icon.classList.remove('bi-chat-dots', 'bi-chat-dots-fill');
            icon.classList.add(unread > 0 ? 'bi-chat-dots-fill' : 'bi-chat-dots');
        }

        button.classList.toggle('has-responses', total > 0);
        button.classList.toggle('has-unread', unread > 0);
    }

    function updateButtonSummary(button, summary = {}) {
        if (!button) {
            return;
        }
        const unread = Math.max(
            0,
            Number(
                summary.unread_count ??
                    summary.unread ??
                    button.dataset.unread ??
                    0,
            ),
        );
        const total = Math.max(
            0,
            Number(
                summary.total_responses ??
                    summary.total ??
                    button.dataset.total ??
                    0,
            ),
        );
        button.dataset.unread = String(unread);
        button.dataset.total = String(total);
        applyButtonVisual(button);
    }

    function ensureButtonForStatus(actionsContainer, taskData) {
        if (!actionsContainer || !taskData) {
            return null;
        }
        let button = actionsContainer.querySelector(BUTTON_SELECTOR);
        const shouldShow = ACTIVE_STATUSES.has(taskData.status);

        if (shouldShow && !button) {
            button = createConversationButton(taskData.id);
            const viewButton = actionsContainer.querySelector('.view-task');
            if (viewButton) {
                viewButton.insertAdjacentElement('afterend', button);
            } else if (actionsContainer.firstChild) {
                actionsContainer.insertBefore(button, actionsContainer.firstChild);
            } else {
                actionsContainer.appendChild(button);
            }
            applyButtonVisual(button);
        } else if (!shouldShow && button) {
            button.remove();
            return null;
        }

        if (shouldShow && button && taskData.responses_summary) {
            updateButtonSummary(button, taskData.responses_summary);
        }

        return button || null;
    }

    function createConversationButton(taskId) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'action responses open-task-responses';
        button.dataset.taskId = taskId;
        button.dataset.unread = '0';
        button.dataset.total = '0';
        button.innerHTML = '<span class="icon"><i class="bi bi-chat-dots"></i></span>';
        return button;
    }

    function onGlobalClick(event) {
        const button = event.target.closest(BUTTON_SELECTOR);
        if (button) {
            event.preventDefault();
            openDrawer(button.dataset.taskId, button);
            return;
        }
    }

    function onGlobalKeydown(event) {
        if (event.key === 'Escape' && state.drawer && state.drawer.classList.contains('is-open')) {
            closeDrawer();
        }
    }

    async function openDrawer(taskId, button) {
        if (!taskId || state.loading) {
            return;
        }
        state.loading = true;
        state.button = button || null;

        try {
            const response = await fetch(`/tasks/${taskId}/responses`);
            if (!response.ok) {
                throw new Error('Falha ao carregar respostas');
            }
            const payload = await response.json();
            if (!payload.success) {
                throw new Error(payload.error || 'Falha ao carregar respostas');
            }
            state.currentTaskId = String(taskId);
            state.meta = payload.meta || {};
            renderDrawer(payload.task, payload.meta, payload.responses || []);
            openDrawerUI();
            await markResponsesRead(taskId, button, { updateDrawer: false });
            updateDrawerMeta(state.meta);
            if (button) {
                updateButtonSummary(button, state.meta);
            }
        } catch (error) {
            console.error('[Tasks] Failed to load responses:', error);
            window.alert('N�o foi poss�vel carregar as respostas da tarefa.');
        } finally {
            state.loading = false;
        }
    }

    function openDrawerUI() {
        if (!state.drawer) {
            return;
        }
        state.drawer.classList.add('is-open');
        state.drawer.setAttribute('aria-hidden', 'false');
        document.body.classList.add('task-responses-open');
        if (state.textarea && !state.textarea.disabled) {
            setTimeout(() => state.textarea.focus(), 100);
        }
    }

    function closeDrawer() {
        if (!state.drawer) {
            return;
        }
        state.drawer.classList.remove('is-open');
        state.drawer.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('task-responses-open');
        state.currentTaskId = null;
        state.button = null;
        state.meta = null;
    }

    function renderDrawer(task, meta, responses) {
        if (!state.drawer) {
            return;
        }
        const titleEl = state.drawer.querySelector('#task-response-title');
        if (titleEl) {
            titleEl.textContent = task?.title || 'Respostas';
        }
        if (state.subtitle) {
            const statusLabel = formatStatus(task?.status);
            const participantNames = (meta?.participants || [])
                .map((participant) => participant.name)
                .filter(Boolean);
            const pieces = [];
            if (statusLabel) {
                pieces.push(statusLabel);
            }
            if (participantNames.length) {
                pieces.push(`Participantes: ${participantNames.join(', ')}`);
            }
            state.subtitle.textContent = pieces.join(' • ');
        }
        updateDrawerMeta(meta);
        renderMessages(responses);
    }

    function updateDrawerMeta(meta = {}) {
        if (state.summary) {
            state.summary.innerHTML = '';
            if (meta.last_response) {
                const info = document.createElement('div');
                info.className = 'conversation-last-response';
                const title = document.createElement('span');
                title.className = 'label';
                title.textContent = '�ltima resposta';
                const author = document.createElement('strong');
                author.textContent = meta.last_response.author?.name || 'Usu�rio';
                const time = document.createElement('span');
                time.className = 'timestamp';
                time.textContent = meta.last_response.created_at_display || formatDateForDisplay(meta.last_response.created_at);
                const excerpt = document.createElement('p');
                excerpt.className = 'excerpt';
                excerpt.innerHTML = meta.last_response.body_html || sanitizeText(meta.last_response.body || '');

                info.appendChild(title);
                const metaLine = document.createElement('div');
                metaLine.className = 'meta-line';
                metaLine.appendChild(author);
                if (time.textContent) {
                    const dot = document.createElement('span');
                    dot.className = 'separator';
                    dot.textContent = '•';
                    metaLine.appendChild(dot);
                    metaLine.appendChild(time);
                }
                state.summary.appendChild(info);
                state.summary.appendChild(metaLine);
                state.summary.appendChild(excerpt);
            } else {
                const empty = document.createElement('p');
                empty.className = 'empty-summary';
                empty.textContent = 'Nenhuma resposta registrada ainda.';
                state.summary.appendChild(empty);
            }
        }

        if (state.hint) {
            if (meta.can_post === false) {
                state.hint.textContent = 'A conversa est� bloqueada para este status.';
            } else {
                state.hint.textContent = 'Use Ctrl + Enter para enviar rapidamente.';
            }
        }

        if (state.textarea) {
            const disabled = meta.can_post === false;
            state.textarea.disabled = disabled;
            if (state.form) {
                state.form.querySelector('button[type="submit"]').disabled = disabled;
            }
        }
    }

    function renderMessages(responses = []) {
        if (!state.messages) {
            return;
        }

        const existingMessages = state.messages.querySelectorAll('.response-message');
        existingMessages.forEach((message) => message.remove());

        const emptyState = state.messages.querySelector('[data-empty-messages]');
        if (!responses.length) {
            if (emptyState) {
                emptyState.hidden = false;
            }
            return;
        }

        if (emptyState) {
            emptyState.hidden = true;
        }

        const fragment = document.createDocumentFragment();
        responses.forEach((response) => {
            fragment.appendChild(buildMessageElement(response));
        });
        state.messages.appendChild(fragment);
        scrollMessagesToBottom();
    }

    function appendMessage(response) {
        if (!state.messages) {
            return;
        }
        const emptyState = state.messages.querySelector('[data-empty-messages]');
        if (emptyState) {
            emptyState.hidden = true;
        }
        state.messages.appendChild(buildMessageElement(response));
        scrollMessagesToBottom();
    }

    function buildMessageElement(response) {
        const wrapper = document.createElement('div');
        wrapper.className = 'response-message';
        const authorId = response.author?.id;
        const isMine = response.is_mine !== undefined ? response.is_mine : (authorId !== undefined && authorId === state.currentUserId);
        if (isMine) {
            wrapper.classList.add('mine');
        }
        wrapper.dataset.responseId = response.id;

        const metaLine = document.createElement('div');
        metaLine.className = 'response-meta';
        const authorName = response.author?.name || 'Usuário';
        const timestamp = response.created_at_display || formatDateForDisplay(response.created_at);
        metaLine.textContent = timestamp ? `${authorName} • ${timestamp}` : authorName;

        const body = document.createElement('div');
        body.className = 'response-body';
        if (response.body_html) {
            body.innerHTML = response.body_html;
        } else {
            body.textContent = response.body || '';
        }

        wrapper.appendChild(metaLine);
        wrapper.appendChild(body);
        return wrapper;
    }

    function scrollMessagesToBottom() {
        if (!state.messages) {
            return;
        }
        state.messages.scrollTop = state.messages.scrollHeight;
    }

    async function handleSubmit(event) {
        event.preventDefault();
        if (!state.currentTaskId || !state.form || !state.textarea || state.textarea.disabled) {
            return;
        }
        const body = state.textarea.value.trim();
        if (!body) {
            return;
        }

        const submitButton = state.form.querySelector('button[type="submit"]');
        if (submitButton) {
            submitButton.disabled = true;
        }
        state.textarea.disabled = true;

        try {
            const response = await fetch(`/tasks/${state.currentTaskId}/responses`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': state.csrfToken,
                },
                body: JSON.stringify({ body }),
            });
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || 'Falha ao enviar resposta');
            }
            state.textarea.value = '';
            if (payload.response) {
                appendMessage(payload.response);
            }
            if (state.button) {
                updateButtonSummary(state.button, payload.meta || {});
            }
            state.meta = payload.meta || state.meta;
            updateDrawerMeta(state.meta);
        } catch (error) {
            console.error('[Tasks] Failed to submit response:', error);
            window.alert('N�o foi poss�vel enviar a resposta.');
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
            }
            if (state.textarea) {
                state.textarea.disabled = state.meta?.can_post === false;
                if (!state.textarea.disabled) {
                    state.textarea.focus();
                }
            }
        }
    }

    async function markResponsesRead(taskId, button, options = {}) {
        if (!state.csrfToken) {
            return;
        }
        let targetButton = button;
        if (!targetButton && state.button && state.currentTaskId === String(taskId)) {
            targetButton = state.button;
        }
        try {
            const response = await fetch(`/tasks/${taskId}/responses/read`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': state.csrfToken,
                },
            });
            if (!response.ok) {
                return;
            }
            const payload = await response.json();
            if (payload.success && payload.meta) {
                state.meta = payload.meta;
                if (options.updateDrawer !== false) {
                    updateDrawerMeta(payload.meta);
                }
                if (targetButton) {
                    updateButtonSummary(targetButton, payload.meta);
                }
            }
        } catch (error) {
            console.error('[Tasks] Failed to mark responses as read:', error);
        }
    }

    function handleRealtimeResponse(data) {
        if (!data || !data.task_id) {
            return;
        }
        const taskId = String(data.task_id);
        const button = document.querySelector(`${BUTTON_SELECTOR}[data-task-id="${taskId}"]`);

        if (state.drawer && state.drawer.classList.contains('is-open') && state.currentTaskId === taskId) {
            if (data.response) {
                appendMessage(data.response);
            }
            markResponsesRead(taskId, button, { updateDrawer: true });
            return;
        }

        if (button) {
            const summary = {
                unread_count: parseInt(button.dataset.unread || '0', 10) + 1,
                total_responses: parseInt(button.dataset.total || '0', 10) + 1,
            };
            updateButtonSummary(button, summary);
        }
    }

    function formatStatus(status) {
        switch (status) {
            case 'in_progress':
                return 'Em andamento';
            case 'done':
                return 'Conclu�da';
            case 'pending':
                return 'Pendente';
            default:
                return '';
        }
    }

    function formatDateForDisplay(isoString) {
        if (!isoString) {
            return '';
        }
        try {
            const date = new Date(isoString);
            return date.toLocaleString('pt-BR', {
                dateStyle: 'short',
                timeStyle: 'short',
            });
        } catch (error) {
            return '';
        }
    }

    function sanitizeText(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    return {
        init,
        refreshButtons,
        ensureButtonForStatus,
        updateButtonSummary,
        handleRealtimeResponse,
        markResponsesRead,
    };
})();

// Global variable to store current user ID
let currentUserId = 0;

document.addEventListener('DOMContentLoaded', () => {
    window.scrollTo(0, 0);

    const kanbanElement = document.querySelector('.kanban');
    currentUserId = kanbanElement ? parseInt(kanbanElement.dataset.currentUser || '0', 10) : 0;
    TaskResponses.init({ csrfToken, currentUserId });

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

    client.on('task:response_created', (data) => {
        console.log('[Tasks] Task response created:', data);
        TaskResponses.handleRealtimeResponse(data);
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

    const isSubtask = taskCard.dataset.isSubtask === 'true' || (task && task.parent_id);
    if (isSubtask) {
        if (task) {
            updateTaskCardContent(taskCard, task);
        }
        taskCard.style.opacity = '1';
        taskCard.style.transition = '';
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

    const hadDetailsOpen = taskCard.classList.contains('show-details');
    const wasCollapsed = taskCard.classList.contains('collapsed');
    const hasChildren = Boolean(taskCard.querySelector('.subtasks'));
    const isSubtask = taskCard.dataset.isSubtask === 'true';

    const classNames = ['task-card', `task-status-${taskData.status}`];
    if (hasChildren) {
        classNames.push('has-children');
    }
    if (isSubtask) {
        classNames.push('subtask-card');
    }
    if (hadDetailsOpen) {
        classNames.push('show-details');
    }
    if (wasCollapsed) {
        classNames.push('collapsed');
    }
    taskCard.className = classNames.join(' ');

    // Update subtask status chip
    const statusChip = taskCard.querySelector('[data-subtask-status]');
    if (statusChip) {
        if (taskData.status === 'pending') {
            statusChip.remove();
        } else {
            statusChip.textContent = taskStatusLabel(taskData.status);
            statusChip.className = `subtask-status status-${taskData.status}`;
        }
    } else if (isSubtask && taskData.status !== 'pending') {
        const titleRow = taskCard.querySelector('.task-title-row');
        if (titleRow) {
            const chip = document.createElement('span');
            chip.className = `subtask-status status-${taskData.status}`;
            chip.setAttribute('data-subtask-status', '');
            chip.textContent = taskStatusLabel(taskData.status);
            titleRow.appendChild(chip);
        }
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
    const existingResponsesButton = actionsContainer.querySelector('.open-task-responses');
    const allowSubtask = canCurrentUserAddSubtask(taskData);
    actionsContainer.dataset.allowSubtask = allowSubtask ? 'true' : 'false';

    // Clear all buttons
    actionsContainer.innerHTML = '';

    // Re-add view button
    if (viewButton) {
        actionsContainer.appendChild(viewButton);
    }

    // Conversation button (between details and status actions)
    let responsesButton = existingResponsesButton;
    if (!responsesButton) {
        responsesButton = TaskResponses.ensureButtonForStatus(actionsContainer, taskData);
    } else {
        actionsContainer.appendChild(responsesButton);
        TaskResponses.ensureButtonForStatus(actionsContainer, taskData);
    }

    // Base class adjustments
    actionsContainer.classList.remove('compact');

    const appendSubtaskButton = () => {
        if (!allowSubtask || taskData.status === 'done') {
            return;
        }
        if (subtaskButton) {
            actionsContainer.appendChild(subtaskButton);
        } else {
            const subtaskLink = document.createElement('a');
            subtaskLink.className = 'action subtask';
            subtaskLink.href = `/tasks/new?parent_id=${taskData.id}`;
            subtaskLink.title = 'Subtarefa';
            subtaskLink.innerHTML = '<i class="bi bi-node-plus"></i>';
            actionsContainer.appendChild(subtaskLink);
        }
    };

    // Add status-specific buttons
    if (taskData.status === 'pending') {
        const startBtn = document.createElement('button');
        startBtn.className = 'action start change-status';
        startBtn.dataset.id = taskData.id;
        startBtn.dataset.status = 'in_progress';
        startBtn.title = 'Iniciar';
        startBtn.innerHTML = '<i class="bi bi-play-fill"></i>';
        actionsContainer.appendChild(startBtn);

        appendSubtaskButton();
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

        appendSubtaskButton();
    } else if (taskData.status === 'done') {
        actionsContainer.classList.remove('compact');

        // Only show reopen button if user is creator
        const isCreator = taskData.created_by === currentUserId;

        if (isCreator) {
            const reopenBtn = document.createElement('button');
            reopenBtn.className = 'action reopen change-status';
            reopenBtn.dataset.id = taskData.id;
            reopenBtn.dataset.status = 'in_progress';
            reopenBtn.title = 'Reabrir';
            reopenBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i>';
            actionsContainer.appendChild(reopenBtn);
        }
    } else {
        actionsContainer.classList.remove('compact');
    }

    // Re-add delete button if it existed
    if (deleteButton) {
        actionsContainer.appendChild(deleteButton);
    }

    TaskResponses.refreshButtons(actionsContainer);
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function taskStatusLabel(status) {
    switch (status) {
        case 'in_progress':
            return 'Em andamento';
        case 'done':
            return 'Concluída';
        default:
            return '';
    }
}

function canCurrentUserAddSubtask(taskData) {
    if (!taskData) {
        return false;
    }
    const isCreator = taskData.created_by === currentUserId;
    const isAssignee = taskData.assigned_to === currentUserId;
    if (taskData.status === 'in_progress') {
        return isCreator || isAssignee;
    }
    if (taskData.status === 'pending') {
        return Boolean(taskData.assigned_to) && (isCreator || isAssignee);
    }
    return false;
}

/**
 * Format ISO date string to readable format
 */
function formatDate(isoString) {
    const date = new Date(isoString);
    return date.toLocaleDateString('pt-BR');
}
