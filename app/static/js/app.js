// Project-specific JavaScript.
//
// Rule of thumb (see AGENTS.md): if Flask/Jinja can do it server-side, do it
// there. Only reach for JS for genuine browser-only interactions that
// Basecoat's vendor bundle (basecoat.all.min.js) does not already cover.

// Project-specific JavaScript belongs here. Reusable component behavior lives
// under static/js/components so it can be promoted to KD UI with its template.

// ── Theme (Light / Dark / System) ───────────────────────────────────────────
//
// Genuinely browser-only state: the choice lives in localStorage, per device,
// and is never sent to the server — so there is nothing Jinja could do here.
//
// "System" is the ABSENCE of the localStorage key, not a third stored value.
// That's what layouts/base.html's pre-paint script already assumes:
//   stored ? stored === "dark" : matchMedia("(prefers-color-scheme: dark)")
// Storing a literal "system" string would break that fallback, so set("system")
// removes the key instead.
//
// This replaces Basecoat's window.basecoat.theme.toggle(), which is binary and
// therefore can never return the app to following the phone. Everything that
// changes the theme must go through here, or the two notions of the stored
// value drift apart.
window.estateTheme = (() => {
  const KEY = "themeMode";
  const mql = window.matchMedia("(prefers-color-scheme: dark)");

  // Keep in sync with the pre-paint script and --background in the CSS theme.
  const COLORS = { light: "#ffffff", dark: "#171717" };

  const get = () => {
    try {
      return localStorage.getItem(KEY) || "system";
    } catch (_) {
      return "system";
    }
  };

  const resolve = (mode) => (mode === "system" ? (mql.matches ? "dark" : "light") : mode);

  const apply = () => {
    const effective = resolve(get());
    document.documentElement.classList.toggle("dark", effective === "dark");
    // Single meta, no media query: a media-scoped theme-color would keep
    // showing the OS theme whenever the user picks the opposite one.
    const meta = document.getElementById("theme-color");
    if (meta) meta.setAttribute("content", COLORS[effective]);
  };

  const set = (mode) => {
    try {
      if (mode === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, mode);
    } catch (_) {}
    apply();
    document.dispatchEvent(
      new CustomEvent("estate:themechange", { detail: { mode, effective: resolve(mode) } })
    );
  };

  // Only meaningful in system mode; in an explicit mode the user has opted out
  // of following the OS, so leave their choice alone.
  mql.addEventListener("change", () => {
    if (get() === "system") apply();
  });

  // Reflect the stored mode into the radio group, and drive changes from it.
  // The control renders unchecked server-side because the value is client-only.
  const bindControls = () => {
    const inputs = document.querySelectorAll('input[name="theme-mode"]');
    if (!inputs.length) return;
    const current = get();
    inputs.forEach((input) => {
      input.checked = input.value === current;
      input.addEventListener("change", () => {
        if (input.checked) set(input.value);
      });
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindControls);
  } else {
    bindControls();
  }

  return { get, set, apply, resolve };
})();
