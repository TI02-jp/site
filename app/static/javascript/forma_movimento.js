function setupFormaMovimento(selectId, digitalId, fisicoId) {
    const select = document.getElementById(selectId);
    const digital = document.getElementById(digitalId);
    const fisico = document.getElementById(fisicoId);
    if (!select || !digital || !fisico) {
        return;
    }
    function clearContainer(container) {
        const checkboxes = container.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach(cb => cb.checked = false);
        const textInputs = container.querySelectorAll('input[type="text"]');
        textInputs.forEach(input => input.value = '');
    }

    function update() {
        const value = select.value;
        if (value === 'Digital') {
            digital.style.display = '';
            fisico.style.display = 'none';
            clearContainer(fisico);
        } else if (value === 'Fisico') {
            digital.style.display = 'none';
            fisico.style.display = '';
            clearContainer(digital);
        } else if (value === 'Digital e Físico') {
            digital.style.display = '';
            fisico.style.display = '';
        } else {
            digital.style.display = 'none';
            fisico.style.display = 'none';
            clearContainer(digital);
            clearContainer(fisico);
        }
    }

    select.addEventListener('change', update);
    update();
}
