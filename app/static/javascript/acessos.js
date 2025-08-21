function setupAcessos(containerId, addBtnId, hiddenInputId) {
  const container = document.getElementById(containerId);
  const addBtn = document.getElementById(addBtnId);
  const hiddenInput = document.getElementById(hiddenInputId);
  let acessos = [];
  try {
    acessos = hiddenInput.value ? JSON.parse(hiddenInput.value) : [];
  } catch (e) {
    acessos = [];
  }

  function render() {
    container.innerHTML = '';
    acessos.forEach((p, idx) => {
      const row = document.createElement('div');
      row.className = 'row g-2 mb-2 acesso-item';
      row.innerHTML = `
        <div class="col-md-3"><input type="text" class="form-control acesso-nome" placeholder="Portal" value="${p.acesso || ''}"></div>
        <div class="col-md-3"><input type="text" class="form-control acesso-link" placeholder="Link" value="${p.link || ''}"></div>
        <div class="col-md-2"><input type="text" class="form-control acesso-usuario" placeholder="Usuário" value="${p.usuario || ''}"></div>
        <div class="col-md-2"><input type="text" class="form-control acesso-senha" placeholder="Senha" value="${p.senha || ''}"></div>
        <div class="col-md-2 d-flex align-items-center"><button type="button" class="btn btn-danger btn-sm w-100 remove-acesso" data-idx="${idx}">Remover</button></div>
        <div class="col-12 mt-2"><textarea class="form-control acesso-observacao" placeholder="Observação" rows="2">${p.observacao || ''}</textarea></div>
      `;
      container.appendChild(row);
    });
    bindActions();
    updateHidden();
  }

  function bindActions() {
    container.querySelectorAll('.remove-acesso').forEach(btn => {
      btn.addEventListener('click', function () {
        const index = this.getAttribute('data-idx');
        acessos.splice(index, 1);
        render();
      });
    });
  }

  function updateHidden() {
    const items = container.querySelectorAll('.acesso-item');
    acessos = Array.from(items).map(item => ({
      acesso: item.querySelector('.acesso-nome').value,
      link: item.querySelector('.acesso-link').value,
      usuario: item.querySelector('.acesso-usuario').value,
      senha: item.querySelector('.acesso-senha').value,
      observacao: item.querySelector('.acesso-observacao').value,
    }));
    hiddenInput.value = JSON.stringify(acessos);
  }

  addBtn.addEventListener('click', function () {
    acessos.push({ acesso: '', link: '', usuario: '', senha: '', observacao: '' });
    render();
  });

  container.addEventListener('input', updateHidden);
  render();
}
