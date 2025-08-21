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
      row.className = 'row g-2 mb-2 prefeitura-item';
      row.innerHTML = `
        <div class="col-md-3"><input type="text" class="form-control prefeitura-acesso" placeholder="Acesso" value="${p.acesso || ''}"></div>
        <div class="col-md-3"><input type="text" class="form-control prefeitura-link" placeholder="Link" value="${p.link || ''}"></div>
        <div class="col-md-2"><input type="text" class="form-control prefeitura-usuario" placeholder="Usuário" value="${p.usuario || ''}"></div>
        <div class="col-md-2"><input type="text" class="form-control prefeitura-senha" placeholder="Senha" value="${p.senha || ''}"></div>
        <div class="col-md-2 d-flex align-items-center"><button type="button" class="btn btn-danger btn-sm w-100 remove-prefeitura" data-idx="${idx}">Remover</button></div>
        <div class="col-12 mt-2"><textarea class="form-control prefeitura-observacao" placeholder="Observação" rows="2">${p.observacao || ''}</textarea></div>
      `;
      container.appendChild(row);
    });
    bindActions();
    updateHidden();
  }

  function bindActions() {
    container.querySelectorAll('.remove-prefeitura').forEach(btn => {
      btn.addEventListener('click', function () {
        const index = this.getAttribute('data-idx');
        prefeituras.splice(index, 1);
        render();
      });
    });
  }

  function updateHidden() {
    const items = container.querySelectorAll('.prefeitura-item');
    prefeituras = Array.from(items).map(item => ({
      acesso: item.querySelector('.prefeitura-acesso').value,
      link: item.querySelector('.prefeitura-link').value,
      usuario: item.querySelector('.prefeitura-usuario').value,
      senha: item.querySelector('.prefeitura-senha').value,
      observacao: item.querySelector('.prefeitura-observacao').value,
    }));
    hiddenInput.value = JSON.stringify(prefeituras);
  }

  addBtn.addEventListener('click', function () {
    prefeituras.push({ acesso: '', link: '', usuario: '', senha: '', observacao: '' });
    render();
  });

  container.addEventListener('input', updateHidden);
  render();
}
