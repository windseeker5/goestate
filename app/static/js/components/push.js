// Web Push subscribe/unsubscribe for the "Enable notifications" toggle in the
// sidebar footer (components/push_toggle.html). Registers the service worker
// unconditionally (cheap, and needed before a subscription can exist), but
// only touches Notification permission when the user taps the toggle.
(() => {
  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  async function postJSON(url, body) {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async function currentSubscription(registration) {
    return registration.pushManager.getSubscription();
  }

  async function syncToggle(toggle, registration) {
    if (!("Notification" in window) || Notification.permission === "denied") {
      toggle.disabled = true;
      toggle.checked = false;
      return;
    }
    const sub = await currentSubscription(registration);
    toggle.checked = !!sub;
  }

  async function subscribe(registration, vapidPublicKey) {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return null;
    const sub = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
    });
    await postJSON("/app/push/subscribe", sub.toJSON());
    return sub;
  }

  async function unsubscribe(registration) {
    const sub = await currentSubscription(registration);
    if (!sub) return;
    await postJSON("/app/push/unsubscribe", { endpoint: sub.endpoint });
    await sub.unsubscribe();
  }

  async function init() {
    const toggle = document.querySelector("[data-push-toggle]");
    if (!toggle || !("serviceWorker" in navigator) || !("PushManager" in window)) {
      if (toggle) toggle.closest("[data-push-toggle-wrap]")?.setAttribute("hidden", "");
      return;
    }

    const vapidPublicKey = toggle.dataset.vapidPublicKey;
    if (!vapidPublicKey) {
      toggle.closest("[data-push-toggle-wrap]")?.setAttribute("hidden", "");
      return;
    }

    const registration = await navigator.serviceWorker.register("/sw.js");
    await syncToggle(toggle, registration);

    toggle.addEventListener("change", async () => {
      toggle.disabled = true;
      try {
        if (toggle.checked) {
          const sub = await subscribe(registration, vapidPublicKey);
          if (!sub) toggle.checked = false;
        } else {
          await unsubscribe(registration);
        }
      } finally {
        toggle.disabled = false;
        await syncToggle(toggle, registration);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
