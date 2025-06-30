document.addEventListener('DOMContentLoaded', function () {
    const fileInputs = document.querySelectorAll('input[type="file"][multiple]');
    fileInputs.forEach(function (input) {
        input.addEventListener('paste', function (e) {
            if (!e.clipboardData || !e.clipboardData.items) {
                return;
            }
            const images = Array.from(e.clipboardData.items).filter(function (item) {
                return item.type.startsWith('image/');
            });
            if (images.length === 0) {
                return;
            }
            const existing = Array.from(input.files);
            images.forEach(function (item) {
                const file = item.getAsFile();
                if (file) {
                    existing.push(file);
                }
            });
            const dt = new DataTransfer();
            existing.forEach(function (file) {
                dt.items.add(file);
            });
            input.files = dt.files;
            e.preventDefault();
        });
    });
});
