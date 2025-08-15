document.addEventListener('DOMContentLoaded', () => {
  const cnpjInput = document.getElementById('cnpj');
  const buscarBtn = document.getElementById('buscar-cnpj');
  if (!cnpjInput) return;

  let lastCnpj = null;

  async function buscarDados() {
    const cnpj = cnpjInput.value.replace(/\D/g, '');
    if (cnpj.length !== 14 || cnpj === lastCnpj) return;
    lastCnpj = cnpj;
    try {
      const resp = await fetch(`/api/buscar_cnpj/${cnpj}`);
      if (!resp.ok) return;
      const data = await resp.json();
      Object.entries(data).forEach(([key, value]) => {
        if (!value) return;
        const field = document.getElementById(key);
        if (field && !field.value) {
          field.value = value;
        }
      });
    } catch (err) {
      console.error('Erro ao buscar CNPJ:', err);
    }
  }

  cnpjInput.addEventListener('blur', buscarDados);
  if (buscarBtn) {
    buscarBtn.addEventListener('click', buscarDados);
  }
});
