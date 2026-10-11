// Estate Copilot service worker: exists solely to receive Web Push events
// while the app isn't open (see static/js/components/push.js for the
// subscribe flow). No offline caching — this app is a live local ledger,
// not something that should ever serve stale data from a cache.

self.addEventListener("push", (event) => {
  let data = { title: "Estate Copilot", body: "You have a new update.", url: "/app" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (_) {}

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
      data: { url: data.url },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/app";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(url) && "focus" in client) return client.focus();
      }
      if (windowClients.length > 0 && "focus" in windowClients[0]) {
        windowClients[0].navigate(url);
        return windowClients[0].focus();
      }
      return clients.openWindow(url);
    })
  );
});
