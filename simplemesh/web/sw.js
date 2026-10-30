// Lets the page show a notification through the browser (Android only allows that from a service worker) and
// bring the page forward when it is tapped. It caches nothing: the fetch handler only passes requests on (some phone
// browsers won't offer to install a page as an app without one).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(list => {
    const open = list.find(w => "focus" in w);
    return open ? open.focus() : self.clients.openWindow("/");
  }));
});
