/* Complementos de interface. Busca, autenticação e gravações pertencem às views Django. */
'use strict';
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-password-toggle]').forEach(button => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.passwordToggle);
      if (!input) return;
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(visible));
      button.setAttribute('aria-label', visible ? 'Ocultar senha' : 'Mostrar senha');
    });
  });
  const upload = document.querySelector('.django-form input[type="file"][name="imagem"]');
  const image = document.getElementById('preview-imagem');
  let imageUrl;
  if (upload && image) upload.addEventListener('change', () => {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    const file = upload.files[0];
    if (!file || !file.type.startsWith('image/')) {
      image.hidden = true;
      image.removeAttribute('src');
      return;
    }
    imageUrl = URL.createObjectURL(file);
    image.src = imageUrl;
    image.hidden = false;
  });
  const quantity = document.querySelector('[data-ticket-quantity]');
  const total = document.querySelector('[data-ticket-total]');
  if (quantity && total) {
    const formatter = new Intl.NumberFormat('pt-BR', {
      style: 'currency',
      currency: 'BRL'
    });
    const updateTotal = () => {
      const min = Number(quantity.min) || 1;
      const max = Number(quantity.max) || 10;
      const number = Math.min(max, Math.max(min, Math.trunc(Number(quantity.value)) || min));
      quantity.value = String(number);
      total.textContent = formatter.format(number * Number(total.dataset.price));
      document.querySelectorAll('[data-quantity-step]').forEach(button => {
        button.disabled = Number(button.dataset.quantityStep) < 0 ? number <= min :
          number >= max;
      });
    };
    document.querySelectorAll('[data-quantity-step]').forEach(button => {
      button.addEventListener('click', () => {
        quantity.value = String(Number(quantity.value) + Number(button.dataset
          .quantityStep));
        updateTotal();
      });
    });
    quantity.addEventListener('change', updateTotal);
    updateTotal();
  }
});

// Compra por lotes: o total no navegador é informativo; o backend calcula o preço.
document.addEventListener('DOMContentLoaded', () => {
  const form = document.querySelector('[data-order-form]');
  if (!form) return;
  const fields = form.querySelectorAll('[data-batch-price]');
  const display = form.querySelector('[data-batch-total]');
  const currency = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  const updateTotal = () => {
    let amount = 0;
    fields.forEach(field => {
      if (field.disabled) return;
      const quantity = Number(field.value);
      const price = Number(field.dataset.batchPrice);
      if (Number.isFinite(quantity) && Number.isFinite(price) && quantity > 0) {
        amount += Math.trunc(quantity) * price;
      }
    });
    if (display) display.textContent = currency.format(amount);
  };
  fields.forEach(field => field.addEventListener('input', updateTotal));
  updateTotal();
  const originalButton = form.querySelector('button[type="submit"]');
  const originalContent = originalButton ? originalButton.cloneNode(true) : null;
  window.addEventListener('pageshow', () => {
    if (!originalButton || !originalContent) return;
    originalButton.disabled = false;
    originalButton.replaceChildren(...Array.from(originalContent.childNodes, node => node.cloneNode(true)));
  });
  form.addEventListener('submit', () => {
    if (!form.checkValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = true;
      button.textContent = 'Criando pedido...';
    }
  });
});
// Carrossel de destaques da home.
// Carrossel de destaques da home.
document.addEventListener("DOMContentLoaded", () => {
  const el = document.querySelector(".hero-swiper");
  if (!el || typeof Swiper === "undefined") return;

  const total = el.querySelectorAll(".swiper-slide").length;

  new Swiper(el, {
    centeredSlides: true,
    slidesPerView: "auto",
    spaceBetween: 16,
    loop: total >= 5, // Mantém a regra original: só ativa com 5+
    loopAdditionalSlides: 2,
    watchOverflow: true,
    speed: 500,
    autoplay: total > 1 ? { delay: 5000, disableOnInteraction: false } : false,
    navigation: {
      nextEl: el.querySelector(".swiper-button-next"),
      prevEl: el.querySelector(".swiper-button-prev"),
    },
    pagination: { el: el.querySelector(".swiper-pagination"), clickable: true },
  });
});