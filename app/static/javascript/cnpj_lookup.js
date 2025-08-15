document.addEventListener('DOMContentLoaded', () => {
  const cnpjInput = document.getElementById('cnpj');
  if (!cnpjInput) return;

  cnpjInput.addEventListener('blur', async () => {
    const cnpj = cnpjInput.value.replace(/\D/g, '');
    if (cnpj.length !== 14) return;
    try {
      const resp = await fetch(`/api/buscar_cnpj/${cnpj}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.nome_empresa) {
        const nomeField = document.getElementById('nome_empresa');
        if (nomeField && !nomeField.value) {
          nomeField.value = data.nome_empresa;
        }
      }
      if (data.data_abertura) {
        const aberturaField = document.getElementById('data_abertura');
        if (aberturaField && !aberturaField.value) {
          aberturaField.value = data.data_abertura;
        }
      }
      if (data.socio_administrador) {
        const socioField = document.getElementById('socio_administrador');
        if (socioField && !socioField.value) {
          socioField.value = data.socio_administrador;
        }
      }
      if (data.atividade_principal) {
        const atividadeField = document.getElementById('atividade_principal');
        if (atividadeField && !atividadeField.value) {
          atividadeField.value = data.atividade_principal;
        }
      }
    } catch (err) {
      console.error('Erro ao buscar CNPJ:', err);
    }
  });
});
