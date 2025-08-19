function setupObservacao(buttonId, containerId, textareaId) {
    const button = document.getElementById(buttonId);
    const container = document.getElementById(containerId);
    const textarea = document.getElementById(textareaId);
    if (!button || !container || !textarea) return;
    if (textarea.value.trim() !== '') {
        container.style.display = 'block';
        button.style.display = 'none';
    }
    button.addEventListener('click', () => {
        container.style.display = 'block';
        button.style.display = 'none';
    });
}
