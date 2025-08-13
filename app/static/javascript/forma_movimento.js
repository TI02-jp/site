function setupFormaMovimento(selectId, digitalId, fisicoId) {
    const select = document.getElementById(selectId);
    const digital = document.getElementById(digitalId);
    const fisico = document.getElementById(fisicoId);
    if (!select || !digital || !fisico) {
        return;
    }
    function update() {
        const value = select.value;
        if (value === 'Digital') {
            digital.style.display = '';
            fisico.style.display = 'none';
        } else if (value === 'Fisico') {
            digital.style.display = 'none';
            fisico.style.display = '';
        } else if (value === 'Digital e Físico') {
            digital.style.display = '';
            fisico.style.display = '';
        } else {
            digital.style.display = 'none';
            fisico.style.display = 'none';
        }
    }
    select.addEventListener('change', update);
    update();
}
