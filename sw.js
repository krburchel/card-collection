// Card Collection moved to https://kburchtree.cloud/card-collection/.
// This worker replaces the old offline copy: it deletes this app's caches (only
// its own; every krburchel.github.io app shares one cache store), removes
// itself, and reloads open windows so they land on the "moved" page.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith("cc-")).map((k) => caches.delete(k)));
    await self.registration.unregister();
    const windows = await self.clients.matchAll({ type: "window" });
    windows.forEach((w) => w.navigate(w.url));
  })());
});
