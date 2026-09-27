'use strict';
window.showToast = (message, error = false) => {
  const toast = document.querySelector('#toast');
  if (!toast) return;
  toast.textContent = message;
  toast.classList.toggle('error', error);
  toast.hidden = false;
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => { toast.hidden = true; }, 4500);
};
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
document.querySelectorAll('.copy-link').forEach(button => {
  button.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(button.dataset.url); showToast('链接已复制，可以发给同行的朋友了。'); }
    catch { window.prompt('复制这条链接分享行程：', button.dataset.url); }
  });
});
document.querySelectorAll('input[required],select[required],textarea[required]').forEach(input => input.setAttribute('aria-required', 'true'));
