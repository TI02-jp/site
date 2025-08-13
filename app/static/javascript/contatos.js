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
      const wrapper = document.createElement('div');
      wrapper.className = 'mb-3 contato-item';
      const endereco = c.endereco || c.valor || '';
      const nome = c.nome || '';
      wrapper.innerHTML = `
        <div class="row g-2">
          <div class="col-md-5 mb-1 mb-md-0">
            <input type="text" class="form-control contato-nome" value="${nome}" placeholder="Nome do contato">
          </div>
          <div class="col-md-5 mb-1 mb-md-0">
            <select class="form-select contato-tipo">
              <option value="email" ${c.tipo === 'email' ? 'selected' : ''}>E-mail</option>
              <option value="telefone" ${c.tipo === 'telefone' ? 'selected' : ''}>Telefone</option>
              <option value="whatsapp" ${c.tipo === 'whatsapp' ? 'selected' : ''}>Whatsapp</option>
              <option value="skype" ${c.tipo === 'skype' ? 'selected' : ''}>Skype</option>
              <option value="acessorias" ${c.tipo === 'acessorias' ? 'selected' : ''}>Acessórias</option>
            </select>
          </div>
          <div class="col-md-2 d-flex align-items-center">
            <button type="button" class="btn btn-danger btn-sm w-100" data-idx="${idx}">Remover</button>
          </div>
        </div>
        <div class="row g-2 mt-1">
          <div class="col-12">
            <input type="text" class="form-control contato-endereco" value="${endereco}" placeholder="Endereço do contato">
          </div>
        </div>`;
      container.appendChild(wrapper);
      setupEnderecoField(wrapper);
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
    container.querySelectorAll('.contato-item').forEach((item, idx) => {
      const nome = item.querySelector('.contato-nome').value;
      const tipo = item.querySelector('.contato-tipo').value;
      const endereco = item.querySelector('.contato-endereco').value;
      contatos[idx] = { nome, tipo, endereco };
    });
    hiddenInput.value = JSON.stringify(contatos);
  }

  function setupEnderecoField(row) {
    const tipoSelect = row.querySelector('.contato-tipo');
    const enderecoInput = row.querySelector('.contato-endereco');

    function handlePhoneInput() {
      let digits = enderecoInput.value.replace(/\D/g, '').slice(0, 11);
      if (digits.length > 6) {
        enderecoInput.value = `(${digits.slice(0,2)}) ${digits.slice(2,7)}-${digits.slice(7)}`;
      } else if (digits.length > 2) {
        enderecoInput.value = `(${digits.slice(0,2)}) ${digits.slice(2)}`;
      } else {
        enderecoInput.value = digits;
      }
    }

    function applyMask() {
      const tipo = tipoSelect.value;
      enderecoInput.removeEventListener('input', handlePhoneInput);
      enderecoInput.type = 'text';
      enderecoInput.removeAttribute('pattern');

      if (tipo === 'email') {
        enderecoInput.type = 'email';
      } else if (tipo === 'telefone' || tipo === 'whatsapp') {
        enderecoInput.type = 'tel';
        enderecoInput.pattern = '\\d{10,11}';
        handlePhoneInput();
        enderecoInput.addEventListener('input', handlePhoneInput);
      }
    }

    tipoSelect.addEventListener('change', applyMask);
    applyMask();
  }

  addBtn.addEventListener('click', function () {
    contatos.push({ nome: '', tipo: 'email', endereco: '' });
    render();
  });

  container.addEventListener('change', updateHidden);
  container.addEventListener('input', updateHidden);
  render();
}
