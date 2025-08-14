function atualizarHoraUTC3() {
  const options = {
    timeZone: 'America/Sao_Paulo',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  };
  const now = new Date().toLocaleTimeString('pt-BR', options);
  const elementoHora = document.getElementById('current-time');
  if (elementoHora) {
    elementoHora.textContent = now;
  }
}

setInterval(atualizarHoraUTC3, 1000);
atualizarHoraUTC3();
