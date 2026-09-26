/*
 * The phone's service worker (web/phone.py). A push from the server is empty:
 * it only wakes this, which asks the server with the phone's cookie for the
 * alerts not shown yet and shows them. Served from the service's root, so
 * that its scope is the page's.
 */
self.addEventListener('push', (event) => {
  event.waitUntil((async () => {
    let shown = 0;
    try {
      const answer = await fetch('api/phone/alerts', { credentials: 'same-origin' });
      const data = await answer.json();
      for (const alert of data.alerts || []) {
        await self.registration.showNotification(data.title, {
          body: alert.text, tag: `${alert.id}:${alert.at}`, icon: 'static/apple-touch-icon.png' });
        shown += 1;
      }
    } catch (error) { /* shown below */ }
    // a push must show something: without it the browser shows its own line
    if (!shown) {
      await self.registration.showNotification('parity-deriva', {
        body: 'open the page to see what changed', tag: 'parity-deriva' });
    }
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: 'window' }).then((open) => (
    open.length ? open[0].focus() : self.clients.openWindow('phone'))));
});
