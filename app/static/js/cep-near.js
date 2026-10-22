/* Busca por CEP no catálogo.
   Sem este arquivo o formulário continua funcionando: o servidor valida o CEP. */
(() => {
  const form = document.querySelector("[data-near-form]");
  if (!form) return;

  const input = form.querySelector("[data-cep-input]");
  const radius = form.querySelector("[data-radius]");
  const button = form.querySelector("[data-submit]");
  const label = form.querySelector("[data-submit-label]");

  // 20040020 -> 20040-020
  const mask = (value) => {
    const digits = value.replace(/\D/g, "").slice(0, 8);
    return digits.length > 5 ? `${digits.slice(0, 5)}-${digits.slice(5)}` : digits;
  };

  input.value = mask(input.value);

  input.addEventListener("input", () => {
    input.value = mask(input.value);
    input.setCustomValidity("");
  });

  input.addEventListener("invalid", () => {
    input.setCustomValidity("Digite os 8 números do CEP, como 20040-020.");
  });

  // A consulta do CEP acontece no servidor e pode levar alguns segundos.
  // Busca só por texto é imediata, então o botão só trava quando há CEP.
  form.addEventListener("submit", () => {
    if (!input.value) return;
    button.disabled = true;
    label.textContent = "Buscando…";
  });

  // Trocar a distância refaz a busca, desde que o CEP já esteja completo.
  if (radius) {
    radius.addEventListener("change", () => {
      if (input.value && input.checkValidity()) form.requestSubmit();
    });
  }

  // Voltar pelo botão do navegador não deve deixar o botão travado.
  window.addEventListener("pageshow", () => {
    button.disabled = false;
    label.textContent = label.dataset.label;
  });
})();