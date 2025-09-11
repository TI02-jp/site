// Schedule meeting reminders 10 minutes before start
// This script fetches upcoming meetings and shows a popup reminder
// for meetings involving the current user.

document.addEventListener('DOMContentLoaded', function() {
    const username = document.body.dataset.username;
    if (!username) {
        return;
    }
    const scheduled = new Map();

    function scheduleEvents(events) {
        const now = new Date();
        events.forEach(function(ev) {
            const id = String(ev.id);
            const existing = scheduled.get(id);
            if (existing) {
                clearTimeout(existing);
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
            const reminderTime = new Date(start.getTime() - 10 * 60 * 1000);
            const delay = reminderTime.getTime() - now.getTime();
            const showReminder = function() {
                alert(`Lembrete 10 minutos antes da reunião ${ev.title}`);
            };
            if (delay > 0) {
                scheduled.set(id, setTimeout(function() {
                    showReminder();
                    scheduled.set(id, null);
                }, delay));
            } else if (start > now) {
                showReminder();
                scheduled.set(id, null);
            }
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
                const t = scheduled.get(String(data.id));
                if (t) {
                    clearTimeout(t);
                }
                scheduled.delete(String(data.id));
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
