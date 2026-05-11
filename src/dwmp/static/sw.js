self.addEventListener("push", (event) => {
  if (!event.data) return;
  const { title, body, url } = event.data.json();
  event.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      icon: "/static/icon.png",
      badge: "/static/icon.png",
      data: { url: url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((list) => {
        const target = event.notification.data.url;
        const existing = list.find(
          (c) => new URL(c.url).pathname === target && "focus" in c
        );
        return existing ? existing.focus() : clients.openWindow(target);
      })
  );
});
