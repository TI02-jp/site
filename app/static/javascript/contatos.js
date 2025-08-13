function setupContatos(containerId, addBtnId, hiddenInputId) {
  const container = document.getElementById(containerId);
  const addBtn = document.getElementById(addBtnId);
  const hiddenInput = document.getElementById(hiddenInputId);
  let contatos = [];
  try {
    contatos = hiddenInput.value ? JSON.parse(hiddenInput.value) : [];
  } catch (e) {
    contatos = [];
  }
  function render() {
    container.innerHTML = '';
    contatos.forEach((c, idx) => {
      const row = document.createElement('div');
      row.className = 'row mb-2';
      const endereco = c.endereco || c.valor || '';
      row.innerHTML = `
        <div class="col-md-5">
          <select class="form-select contato-tipo">
            <option value="email" ${c.tipo === 'email' ? 'selected' : ''}>E-mail</option>
            <option value="telefone" ${c.tipo === 'telefone' ? 'selected' : ''}>Telefone</option>
            <option value="whatsapp" ${c.tipo === 'whatsapp' ? 'selected' : ''}>Whatsapp</option>
            <option value="skype" ${c.tipo === 'skype' ? 'selected' : ''}>Skype</option>
            <option value="acessorias" ${c.tipo === 'acessorias' ? 'selected' : ''}>Acessórias</option>
          </select>
        </div>
        <div class="col-md-5">
          <input type="text" class="form-control contato-endereco" value="${endereco}" placeholder="Endereço do contato">
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
      btn.addEventListener('click', function () {
        const index = this.getAttribute('data-idx');
        contatos.splice(index, 1);
        render();
      });
    });
  }
  function updateHidden() {
    container.querySelectorAll('.row').forEach((row, idx) => {
      const tipo = row.querySelector('.contato-tipo').value;
      const endereco = row.querySelector('.contato-endereco').value;
      contatos[idx] = { tipo, endereco };
    });
    hiddenInput.value = JSON.stringify(contatos);
  }
  addBtn.addEventListener('click', function () {
    contatos.push({ tipo: 'email', endereco: '' });
    render();
  });
  container.addEventListener('change', updateHidden);
  container.addEventListener('input', updateHidden);
  render();
}
