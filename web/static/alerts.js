/*
 * The alerts open on this server (web/notify.py) as banners under the page's
 * header, each with a button that dismisses it; asked again every minute, as
 * often as the server looks for new ones. On the live and logs pages.
 */
(() => {
  const box = document.createElement('div');
  box.id = 'alert-banners';
  box.setAttribute('role', 'status');
  document.querySelector('header').after(box);

  async function dismiss(id) {
    await fetch('api/alerts/dismiss', { method: 'POST', body: JSON.stringify({ id }),
                                        headers: { 'X-Parity-Deriva': '1' } });
    show();
  }

  async function show() {
    let open;
    try {
      const answer = await fetch('api/alerts');
      if (!answer.ok) return;
      ({ open } = await answer.json());
    } catch (error) {
      return;
    }
    box.textContent = '';
    for (const alert of open) {
      const row = document.createElement('div');
      row.className = `alert-banner ${alert.level}`;
      const text = document.createElement('span');
      text.className = 'text';
      text.textContent = `${new Date(alert.at).toISOString().slice(0, 16).replace('T', ' ')} UTC · ${alert.text}`;
      const close = document.createElement('button');
      close.type = 'button';
      close.dataset.icon = 'close';
      close.textContent = 'dismiss';
      close.addEventListener('click', () => dismiss(alert.id));
      row.append(text, close);
      box.appendChild(row);
    }
  }

  show();
  setInterval(show, 60000);
})();
