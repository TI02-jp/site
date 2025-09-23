(function () {
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-video-collapse]').forEach(function (element) {
            if (element.classList.contains('show')) {
                bootstrap.Collapse.getOrCreateInstance(element, { toggle: false }).show();
            }
        });

        const players = Array.from(document.querySelectorAll('.video-card video'));
        players.forEach(function (player) {
            player.addEventListener('play', function () {
                players.forEach(function (other) {
                    if (other !== player) {
                        other.pause();
                    }
                });
            });
        });
    });
})();
