function setupPrefeituras(containerId, addBtnId, hiddenInputId) {
  const container = document.getElementById(containerId);
  const addBtn = document.getElementById(addBtnId);
  const hiddenInput = document.getElementById(hiddenInputId);
  let prefeituras = [];
  try {
    prefeituras = hiddenInput.value ? JSON.parse(hiddenInput.value) : [];
  } catch (e) {
    prefeituras = [];
  }
  function render() {
    container.innerHTML = '';
    prefeituras.forEach((p, idx) => {
      const row = document.createElement('div');
      row.className = 'row mb-2';
      row.innerHTML = `
        <div class="col-md-4">
          <input type="text" class="form-control prefeitura-link" placeholder="Link" value="${p.link || ''}">
        </div>
        <div class="col-md-3">
          <input type="text" class="form-control prefeitura-usuario" placeholder="Usuário" value="${p.usuario || ''}">
        </div>
        <div class="col-md-3">
          <input type="text" class="form-control prefeitura-senha" placeholder="Senha" value="${p.senha || ''}">
        </div>
        <div class="col-md-2 d-flex align-items-center">
          <button type="button" class="btn btn-danger btn-sm" data-idx="${idx}">Remover</button>
        </div>`;
      container.appendChild(row);
    });
    bindRemove();
    updateHidden();
  }
  function bindRemove() {
    container.querySelectorAll('button[data-idx]').forEach(btn => {
      btn.addEventListener('click', function() {
        const index = this.getAttribute('data-idx');
        prefeituras.splice(index, 1);
        render();
      });
    });
  }
  function updateHidden() {
    container.querySelectorAll('.row').forEach((row, idx) => {
      const link = row.querySelector('.prefeitura-link').value;
      const usuario = row.querySelector('.prefeitura-usuario').value;
      const senha = row.querySelector('.prefeitura-senha').value;
      prefeituras[idx] = { link, usuario, senha };
    });
    hiddenInput.value = JSON.stringify(prefeituras);
  }
  addBtn.addEventListener('click', function() {
    prefeituras.push({ link: '', usuario: '', senha: '' });
    render();
  });
  container.addEventListener('change', updateHidden);
  container.addEventListener('input', updateHidden);
  render();
}
