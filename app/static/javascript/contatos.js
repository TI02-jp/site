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
      const nome = c.nome || '';
      const meios = Array.isArray(c.meios) && c.meios.length ? c.meios : [{ tipo: 'email', endereco: '' }];
      wrapper.innerHTML = `
        <div class="row g-2">
          <div class="col-md-10 mb-1 mb-md-0">
            <input type="text" class="form-control contato-nome" value="${nome}" placeholder="Nome do contato">
          </div>
          <div class="col-md-2 d-flex align-items-center">
            <button type="button" class="btn btn-danger btn-sm w-100 remove-contato" data-idx="${idx}">Remover</button>
          </div>
        </div>
        <div class="meios-container"></div>
        <button type="button" class="btn btn-secondary btn-sm mt-2 add-meio" data-idx="${idx}">Adicionar contato</button>`;
      container.appendChild(wrapper);

      const meiosContainer = wrapper.querySelector('.meios-container');
      meios.forEach((m, mIdx) => {
        const meioDiv = document.createElement('div');
        meioDiv.className = 'meio-item mt-2';
        const endereco = m.endereco || '';
        meioDiv.innerHTML = `
          <div class="row g-2">
            <div class="col-md-5 mb-1 mb-md-0">
              <select class="form-select contato-tipo">
                <option value="email" ${m.tipo === 'email' ? 'selected' : ''}>E-mail</option>
                <option value="telefone" ${m.tipo === 'telefone' ? 'selected' : ''}>Telefone</option>
                <option value="whatsapp" ${m.tipo === 'whatsapp' ? 'selected' : ''}>Whatsapp</option>
                <option value="acessorias" ${m.tipo === 'acessorias' ? 'selected' : ''}>Acessórias</option>
              </select>
            </div>
            <div class="col-md-2 d-flex align-items-center">
              <button type="button" class="btn btn-danger btn-sm w-100 remove-meio" data-cidx="${idx}" data-midx="${mIdx}">Remover</button>
            </div>
          </div>
          <div class="row g-2 mt-1">
            <div class="col-12">
              <input type="text" class="form-control contato-endereco" value="${endereco}" placeholder="Endereço do contato">
            </div>
          </div>`;
        meiosContainer.appendChild(meioDiv);
        setupEnderecoField(meioDiv);
      });
    });
    bindActions();
    updateHidden();
  }

  function bindActions() {
    container.querySelectorAll('.remove-contato').forEach(btn => {
      btn.addEventListener('click', function () {
        const index = this.getAttribute('data-idx');
        contatos.splice(index, 1);
        render();
      });
    });
    container.querySelectorAll('.add-meio').forEach(btn => {
      btn.addEventListener('click', function () {
        const index = this.getAttribute('data-idx');
        contatos[index].meios.push({ tipo: 'email', endereco: '' });
        render();
      });
    });
    container.querySelectorAll('.remove-meio').forEach(btn => {
      btn.addEventListener('click', function () {
        const cIdx = this.getAttribute('data-cidx');
        const mIdx = this.getAttribute('data-midx');
        contatos[cIdx].meios.splice(mIdx, 1);
        if (contatos[cIdx].meios.length === 0) {
          contatos[cIdx].meios.push({ tipo: 'email', endereco: '' });
        }
        render();
      });
    });
  }

  function updateHidden() {
    container.querySelectorAll('.contato-item').forEach((item, idx) => {
      const nome = item.querySelector('.contato-nome').value;
      const meios = [];
      item.querySelectorAll('.meio-item').forEach(meio => {
        const tipo = meio.querySelector('.contato-tipo').value;
        const endereco = meio.querySelector('.contato-endereco').value;
        meios.push({ tipo, endereco });
      });
      contatos[idx] = { nome, meios };
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
        enderecoInput.pattern = '\\(\\d{2}\\)\\s?\\d{4,5}-\\d{4}';
        handlePhoneInput();
        enderecoInput.addEventListener('input', handlePhoneInput);
      }
    }

    tipoSelect.addEventListener('change', applyMask);
    applyMask();
  }

  addBtn.addEventListener('click', function () {
    contatos.push({ nome: '', meios: [{ tipo: 'email', endereco: '' }] });
    render();
  });

  container.addEventListener('change', updateHidden);
  container.addEventListener('input', updateHidden);
  render();
}
