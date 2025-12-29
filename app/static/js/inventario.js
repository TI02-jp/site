document.addEventListener('DOMContentLoaded', function() {
    const allowedTributacoes = window.inventarioConfig?.allowedTributacoes || [];
    const statusChoices = window.inventarioConfig?.statusChoices || [];
    const tributacaoStorageKey = 'inventario-tributacao-filters';
    const statusStorageKey = 'inventario-status-filters';
    const currentUrl = new URL(window.location.href);
    const clearTributacao = currentUrl.searchParams.get('clear_tributacao') === '1';
    const clearStatus = currentUrl.searchParams.get('clear_status') === '1';
    const currentTributacoes = currentUrl.searchParams.getAll('tributacao');
    const currentStatus = currentUrl.searchParams.getAll('status');

    // Funcionalidade de pesquisa
    const searchInput = document.getElementById('searchInput');
    const clearSearchBtn = document.getElementById('clearSearch');
    const tableRows = document.querySelectorAll('#inventarioTable tbody tr');

    if (searchInput) {
        searchInput.addEventListener('input', function() {
            const searchTerm = this.value.toLowerCase().trim();

            tableRows.forEach(row => {
                const empresaId = row.querySelector('td:first-child')?.textContent.toLowerCase() || '';
                const razaoSocial = row.querySelector('td:nth-child(2)')?.textContent.toLowerCase() || '';

                if (empresaId.includes(searchTerm) || razaoSocial.includes(searchTerm)) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
        });

        clearSearchBtn.addEventListener('click', function() {
            searchInput.value = '';
            tableRows.forEach(row => {
                row.style.display = '';
            });
            searchInput.focus();
        });
    }

    function sanitizeTributacoes(values) {
        return values.filter((value) => allowedTributacoes.includes(value));
    }

    function sanitizeStatus(values) {
        return values.filter((value) => statusChoices.includes(value));
    }

    // Gerenciar filtro de tributação
    if (clearTributacao) {
        try {
            localStorage.removeItem(tributacaoStorageKey);
        } catch (_) {
            /* armazenamento indisponivel */
        }
    } else if (currentTributacoes.length) {
        const sanitized = sanitizeTributacoes(currentTributacoes);
        try {
            localStorage.setItem(tributacaoStorageKey, JSON.stringify(sanitized));
        } catch (_) {
            /* armazenamento indisponivel */
        }
    } else {
        let savedTributacoes = [];
        try {
            savedTributacoes = JSON.parse(localStorage.getItem(tributacaoStorageKey) || '[]');
        } catch (_) {
            savedTributacoes = [];
        }
        const sanitized = sanitizeTributacoes(Array.isArray(savedTributacoes) ? savedTributacoes : []);
        if (sanitized.length) {
            sanitized.forEach((value) => currentUrl.searchParams.append('tributacao', value));
            window.location.replace(currentUrl.toString());
            return;
        }
    }

    // Gerenciar filtro de status
    if (clearStatus) {
        try {
            localStorage.removeItem(statusStorageKey);
        } catch (_) {
            /* armazenamento indisponivel */
        }
    } else if (currentStatus.length) {
        const sanitized = sanitizeStatus(currentStatus);
        try {
            localStorage.setItem(statusStorageKey, JSON.stringify(sanitized));
        } catch (_) {
            /* armazenamento indisponivel */
        }
    } else {
        let savedStatus = [];
        try {
            savedStatus = JSON.parse(localStorage.getItem(statusStorageKey) || '[]');
        } catch (_) {
            savedStatus = [];
        }
        const sanitized = sanitizeStatus(Array.isArray(savedStatus) ? savedStatus : []);
        if (sanitized.length) {
            sanitized.forEach((value) => currentUrl.searchParams.append('status', value));
            window.location.replace(currentUrl.toString());
            return;
        }
    }

    // Debounce para evitar requests excessivos
    let debounceTimers = {};

    const MAX_MONEY_CENTS = 100000000000;

    // Função para formatar valor como moeda
    function formatMoney(value) {
        // Remover tudo exceto números
        let numbers = value.replace(/\D/g, '');

        if (!numbers) return '';

        // Converter para número com limite de 1.000.000.000,00
        let amountCents = Math.min(parseInt(numbers, 10), MAX_MONEY_CENTS);
        let amount = amountCents / 100;

        // Formatar como moeda brasileira
        return 'R$ ' + amount.toLocaleString('pt-BR', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }

    // Aplicar máscara de moeda em campos monetários
    document.querySelectorAll('.money-field').forEach(field => {
        field.addEventListener('input', function(e) {
            let cursorPosition = this.selectionStart;
            let oldLength = this.value.length;

            this.value = formatMoney(this.value);

            // Ajustar posição do cursor
            let newLength = this.value.length;
            let diff = newLength - oldLength;
            this.selectionStart = this.selectionEnd = cursorPosition + diff;
        });

        // Formatar ao carregar se já tiver valor
        if (field.value) {
            field.value = formatMoney(field.value);
        }
    });

    // Função para ajustar tamanho da fonte do status
    function adjustStatusFontSize(selectElement) {
        const selectedOption = selectElement.options[selectElement.selectedIndex];
        const text = selectedOption ? selectedOption.text : '';
        const length = text.length;

        selectElement.classList.remove('status-short', 'status-medium', 'status-long');

        if (length === 0) {
            selectElement.classList.add('status-short');
        } else if (length <= 15) {
            selectElement.classList.add('status-short');
        } else if (length <= 25) {
            selectElement.classList.add('status-medium');
        } else {
            selectElement.classList.add('status-long');
        }
    }

    function updateCfopHighlight(row) {
        if (!row) return;
        const encerramentoSelect = row.querySelector('select[data-field="encerramento_fiscal"]');
        const cfopCell = row.querySelector('.col-cfop');

        if (!encerramentoSelect || !cfopCell) return;

        const isEncerrado = encerramentoSelect.value === 'true';
        const hasCfop = !cfopCell.querySelector('.pdf-upload');

        cfopCell.classList.toggle('cfop-missing', isEncerrado && !hasCfop);
    }

    // Aplicar ajuste inicial nos selects de status
    document.querySelectorAll('select[data-field="status"]').forEach(select => {
        adjustStatusFontSize(select);
    });

    tableRows.forEach(row => {
        updateCfopHighlight(row);
    });

    // Atualizar campo editável
    document.querySelectorAll('.editable-field').forEach(field => {
        field.addEventListener('change', function() {
            const row = this.closest('tr');
            const empresaId = row.dataset.empresaId;
            const fieldName = this.dataset.field;
            const value = this.value;

            // Ajustar fonte do status quando mudar
            if (fieldName === 'status') {
                adjustStatusFontSize(this);
            }

            if (fieldName === 'encerramento_fiscal') {
                updateCfopHighlight(row);
            }

            updateInventario(empresaId, fieldName, value, this);
        });

        // Para inputs de texto (não monetários), usar debounce
        if (field.tagName === 'INPUT' && field.type === 'text' && !field.classList.contains('money-field')) {
            field.addEventListener('input', function() {
                const row = this.closest('tr');
                const empresaId = row.dataset.empresaId;
                const fieldName = this.dataset.field;
                const value = this.value;
                const element = this;

                // Limpar timer anterior
                if (debounceTimers[fieldName + empresaId]) {
                    clearTimeout(debounceTimers[fieldName + empresaId]);
                }

                // Criar novo timer
                debounceTimers[fieldName + empresaId] = setTimeout(() => {
                    updateInventario(empresaId, fieldName, value, element);
                }, 1000); // 1 segundo de delay
            });
        }
    });

    // Upload de PDF
    document.querySelectorAll('.pdf-upload').forEach(input => {
        input.addEventListener('change', function() {
            const row = this.closest('tr');
            const empresaId = row.dataset.empresaId;
            const file = this.files[0];

            if (file && file.type === 'application/pdf') {
                uploadPDF(empresaId, file, this);
            } else if (file) {
                alert('Por favor, selecione um arquivo PDF válido.');
                this.value = '';
            }
        });
    });

    // Deletar PDF
    document.querySelectorAll('.delete-pdf-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            if (confirm('Deseja realmente remover este arquivo PDF?')) {
                const row = this.closest('tr');
                const empresaId = row.dataset.empresaId;
                deletePDF(empresaId, this);
            }
        });
    });

    // Upload de arquivo do cliente
    document.querySelectorAll('.cliente-upload').forEach(input => {
        input.addEventListener('change', function() {
            const row = this.closest('tr');
            const empresaId = row.dataset.empresaId;
            const file = this.files[0];

            if (file) {
                uploadClienteFile(empresaId, file, this);
            }
        });
    });

    // Deletar arquivo do cliente
    document.querySelectorAll('.delete-cliente-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            if (confirm('Deseja realmente remover este arquivo?')) {
                const row = this.closest('tr');
                const empresaId = row.dataset.empresaId;
                deleteClienteFile(empresaId, this);
            }
        });
    });

    // Mantém dropdown de tributação aberto ao marcar checkboxes
    const tribDropdownToggle = document.getElementById('tributacaoFilterDropdown');
    if (tribDropdownToggle && bootstrap?.Dropdown) {
        const tribDropdownInstance = bootstrap.Dropdown.getOrCreateInstance(tribDropdownToggle, { autoClose: 'outside' });
        const dropdownParent = tribDropdownToggle.closest('.dropdown');

        if (dropdownParent) {
            dropdownParent.addEventListener('hide.bs.dropdown', function (event) {
                const clickTarget = event?.clickEvent?.target;
                if (!clickTarget) return;

                const insideForm = clickTarget.closest('.tributacao-filter-form');
                const isApply = clickTarget.closest('.tributacao-filter-apply');
                const isClear = clickTarget.closest('.tributacao-filter-clear');

                if (insideForm && !isApply && !isClear) {
                    event.preventDefault();
                }
            });
        }

        // Evita propagação nos checkboxes para não fechar por engano
        document.querySelectorAll('.tributacao-filter-form .form-check-input').forEach(function (input) {
            input.addEventListener('click', function (ev) {
                ev.stopPropagation();
            });
        });
    }

    // Mantém dropdown de status aberto ao marcar checkboxes
    const statusDropdownToggle = document.getElementById('statusFilterDropdown');
    if (statusDropdownToggle && bootstrap?.Dropdown) {
        const statusDropdownInstance = bootstrap.Dropdown.getOrCreateInstance(statusDropdownToggle, { autoClose: 'outside' });
        const statusDropdownParent = statusDropdownToggle.closest('.dropdown');

        if (statusDropdownParent) {
            statusDropdownParent.addEventListener('hide.bs.dropdown', function (event) {
                const clickTarget = event?.clickEvent?.target;
                if (!clickTarget) return;

                const insideForm = clickTarget.closest('.status-filter-form');
                const isApply = clickTarget.closest('.status-filter-apply');
                const isClear = clickTarget.closest('.status-filter-clear');

                if (insideForm && !isApply && !isClear) {
                    event.preventDefault();
                }
            });
        }

        // Evita propagação nos checkboxes para não fechar por engano
        document.querySelectorAll('.status-filter-form .form-check-input').forEach(function (input) {
            input.addEventListener('click', function (ev) {
                ev.stopPropagation();
            });
        });
    }

    function updateInventario(empresaId, field, value, element) {
        // Indicador visual de salvamento
        element.classList.remove('saved', 'error');
        element.classList.add('saving');

        fetch('/api/inventario/update', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                empresa_id: empresaId,
                field: field,
                value: value
            })
        })
        .then(response => response.json())
        .then(data => {
            element.classList.remove('saving');
            if (data.success) {
                // Atualizar valor formatado se retornado
                if (data.value !== undefined && data.value !== value) {
                    element.value = data.value;
                }
                element.classList.add('saved');
                setTimeout(() => element.classList.remove('saved'), 2000);
            } else {
                element.classList.add('error');
                alert('Erro ao salvar: ' + (data.error || 'Erro desconhecido'));
            }
        })
        .catch(error => {
            element.classList.remove('saving');
            element.classList.add('error');
            console.error('Erro:', error);
            alert('Erro ao salvar. Tente novamente.');
        });
    }

    function uploadPDF(empresaId, file, inputElement) {
        const formData = new FormData();
        formData.append('pdf', file);
        const csrfToken = window.csrfToken || '';
        const row = inputElement.closest('tr');

        const container = inputElement.closest('.pdf-upload-container');
        container.innerHTML = '<small class="text-muted">Enviando...</small>';

        fetch(`/api/inventario/upload-pdf/${empresaId}`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken
            },
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                container.innerHTML = `
                    <div class="d-flex gap-1 align-items-center">
                        <a href="${data.url}" target="_blank" class="btn btn-sm btn-outline-primary" title="${data.filename}">
                            <i class="bi bi-file-pdf"></i>
                        </a>
                        <button type="button" class="btn btn-sm btn-outline-danger delete-pdf-btn" title="Remover PDF">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                `;

                updateCfopHighlight(row);

                // Re-anexar evento de deletar
                container.querySelector('.delete-pdf-btn').addEventListener('click', function() {
                    if (confirm('Deseja realmente remover este arquivo PDF?')) {
                        deletePDF(empresaId, this);
                    }
                });
            } else {
                alert('Erro ao fazer upload: ' + (data.error || 'Erro desconhecido'));
                container.innerHTML = '<input type="file" class="form-control form-control-sm pdf-upload" accept=".pdf">';
            }
        })
        .catch(error => {
            console.error('Erro:', error);
            alert('Erro ao fazer upload. Tente novamente.');
            container.innerHTML = '<input type="file" class="form-control form-control-sm pdf-upload" accept=".pdf">';
        });
    }

    function deletePDF(empresaId, btnElement) {
        const container = btnElement.closest('.pdf-upload-container');
        container.innerHTML = '<small class="text-muted">Removendo...</small>';
        const csrfToken = window.csrfToken || '';
        const row = btnElement.closest('tr');

        fetch(`/api/inventario/delete-pdf/${empresaId}`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                container.innerHTML = '<input type="file" class="form-control form-control-sm pdf-upload" accept=".pdf">';

                // Re-anexar evento de upload
                const newInput = container.querySelector('.pdf-upload');
                newInput.addEventListener('change', function() {
                    const file = this.files[0];
                    if (file && file.type === 'application/pdf') {
                        uploadPDF(empresaId, file, this);
                    } else if (file) {
                        alert('Por favor, selecione um arquivo PDF válido.');
                        this.value = '';
                    }
                });

                updateCfopHighlight(row);
            } else {
                alert('Erro ao remover arquivo: ' + (data.error || 'Erro desconhecido'));
                location.reload();
            }
        })
        .catch(error => {
            console.error('Erro:', error);
            alert('Erro ao remover arquivo. Tente novamente.');
            location.reload();
        });
    }

    function uploadClienteFile(empresaId, file, inputElement) {
        const formData = new FormData();
        formData.append('file', file);
        const csrfToken = window.csrfToken || '';

        const container = inputElement.closest('.cliente-upload-container');
        container.innerHTML = '<small class="text-muted">Enviando...</small>';

        fetch(`/api/inventario/upload-cliente-file/${empresaId}`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken
            },
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                container.innerHTML = `
                    <div class="flex-grow-1 d-flex align-items-center">
                        <small class="text-muted text-truncate" style="max-width: 85px;" title="${data.filename}">${data.filename}</small>
                    </div>
                    <a href="${data.url}" target="_blank" class="btn btn-sm btn-outline-primary" title="Visualizar arquivo">
                        <i class="bi bi-eye"></i>
                    </a>
                    <a href="${data.url}" download="${data.filename}" class="btn btn-sm btn-outline-success" title="Baixar arquivo">
                        <i class="bi bi-download"></i>
                    </a>
                    <button type="button" class="btn btn-sm btn-outline-danger delete-cliente-btn" title="Remover arquivo">
                        <i class="bi bi-trash"></i>
                    </button>
                `;

                // Re-anexar evento de deletar
                container.querySelector('.delete-cliente-btn').addEventListener('click', function() {
                    if (confirm('Deseja realmente remover este arquivo?')) {
                        deleteClienteFile(empresaId, this);
                    }
                });
            } else {
                alert('Erro ao fazer upload: ' + (data.error || 'Erro desconhecido'));
                container.innerHTML = '<input type="file" class="form-control form-control-sm cliente-upload">';
            }
        })
        .catch(error => {
            console.error('Erro:', error);
            alert('Erro ao fazer upload. Tente novamente.');
            container.innerHTML = '<input type="file" class="form-control form-control-sm cliente-upload">';
        });
    }

    function deleteClienteFile(empresaId, btnElement) {
        const container = btnElement.closest('.cliente-upload-container');
        container.innerHTML = '<small class="text-muted">Removendo...</small>';
        const csrfToken = window.csrfToken || '';

        fetch(`/api/inventario/delete-cliente-file/${empresaId}`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                container.innerHTML = '<input type="file" class="form-control form-control-sm cliente-upload">';

                // Re-anexar evento de upload
                const newInput = container.querySelector('.cliente-upload');
                newInput.addEventListener('change', function() {
                    const file = this.files[0];
                    if (file) {
                        uploadClienteFile(empresaId, file, this);
                    }
                });
            } else {
                alert('Erro ao remover arquivo: ' + (data.error || 'Erro desconhecido'));
                location.reload();
            }
        })
        .catch(error => {
            console.error('Erro:', error);
            alert('Erro ao remover arquivo. Tente novamente.');
            location.reload();
        });
    }

    // Detectar scroll horizontal e adicionar indicador visual
    const tableWrapper = document.querySelector('.table-wrapper');

    if (tableWrapper) {
        function updateScrollIndicator() {
            const hasScrollRight = tableWrapper.scrollLeft < (tableWrapper.scrollWidth - tableWrapper.clientWidth - 5);

            if (hasScrollRight) {
                tableWrapper.classList.add('has-scroll-right');
            } else {
                tableWrapper.classList.remove('has-scroll-right');
            }
        }

        // Verificar no carregamento
        updateScrollIndicator();

        // Verificar ao rolar
        tableWrapper.addEventListener('scroll', updateScrollIndicator);

        // Verificar ao redimensionar a janela
        window.addEventListener('resize', updateScrollIndicator);
    }
});
