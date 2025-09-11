// Schedule multiple meeting reminders before the start time
// This script fetches upcoming meetings and shows popup reminders
// for meetings involving the current user at 30, 10, 5 and 2 minutes
// before the meeting begins.

document.addEventListener('DOMContentLoaded', function() {
    const username = document.body.dataset.username;
    if (!username) {
        return;
    }
    const scheduled = new Map();
    // Track which reminders have already been displayed so they are only shown once
    const displayed = new Set();

    function scheduleEvents(events) {
        const now = new Date();
        events.forEach(function(ev) {
            const id = String(ev.id);
            const existing = scheduled.get(id);
            if (existing) {
                existing.forEach(function(t) { clearTimeout(t); });
                scheduled.delete(id);
            }
            const participants = ev.participants || [];
            if (ev.creator !== username && !participants.includes(username)) {
                return;
            }
            if (!ev.start) {
                return;
            }
            const start = new Date(ev.start);
            if (isNaN(start.getTime())) {
                return;
            }
            const timers = [];
            [30, 10, 5, 2].forEach(function(mins) {
                const reminderTime = new Date(start.getTime() - mins * 60 * 1000);
                const delay = reminderTime.getTime() - now.getTime();
                const key = `${id}-${mins}`;
                const showReminder = function() {
                    if (displayed.has(key)) {
                        return;
                    }
                    displayed.add(key);
                    alert(`Lembrete ${mins} minutos antes da reunião ${ev.title}`);
                };
                if (delay > 0) {
                    timers.push(setTimeout(showReminder, delay));
                } else if (start > now) {
                    showReminder();
                }
            });
            scheduled.set(id, timers);
        });
    }

    function fetchEvents() {
        fetch('/api/reunioes')
            .then(function(resp) { return resp.json(); })
            .then(scheduleEvents)
            .catch(function() { /* ignore errors */ });
    }

    fetchEvents();

    const source = new EventSource('/api/reunioes/stream');
    source.onmessage = function(evt) {
        try {
            const data = JSON.parse(evt.data);
            if (data.type === 'deleted') {
                const timers = scheduled.get(String(data.id));
                if (timers) {
                    timers.forEach(function(t) { clearTimeout(t); });
                }
                scheduled.delete(String(data.id));
                // Allow reminders to fire again if a meeting with the same id is recreated
                const prefix = `${data.id}-`;
                [...displayed].forEach(function(key) {
                    if (key.startsWith(prefix)) {
                        displayed.delete(key);
                    }
                });
            } else if (data.type === 'updated') {
                scheduleEvents([data.meeting]);
            } else if (data.type === 'created') {
                scheduleEvents([data.meeting]);
            }
        } catch (e) {
            // ignore malformed messages
        }
    };
});
