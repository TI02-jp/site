// Schedule meeting reminders 10 minutes before start
// This script fetches upcoming meetings and shows a popup reminder
// for meetings involving the current user.

document.addEventListener('DOMContentLoaded', function() {
    const username = document.body.dataset.username;
    if (!username) {
        return;
    }
    const scheduled = new Set();

    function scheduleEvents(events) {
        const now = new Date();
        events.forEach(function(ev) {
            const id = String(ev.id);
            if (scheduled.has(id)) {
                return;
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
                scheduled.add(id);
                setTimeout(showReminder, delay);
            } else if (start > now) {
                scheduled.add(id);
                showReminder();
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
    setInterval(fetchEvents, 5 * 60 * 1000);
});
