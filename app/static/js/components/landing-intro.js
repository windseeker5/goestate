// Landing intro timeline (markup: components/landing_intro.html).
//
// One GSAP timeline, five beats, each ending on a labelled "hold" frame. The
// last beat reveals the real hero markup and then removes the intro, so the
// page ends exactly as it looks without the intro.
//
// Draft tools, for reviewing the animation frame by frame:
//   ?intro=debug   beat + timer badge in the corner
//   ?t=6.2         freeze at 6.2 s
//   ?beat=3        freeze on beat 3's hold frame
//   window.lpIntro the timeline, for seeking from Playwright
(() => {
  const root = document.documentElement;
  if (!root.classList.contains("lpi-on") || !window.gsap) return;
  window.lpIntroStarted = true;

  const q = new URLSearchParams(location.search);
  const forced = q.has("intro") || q.has("t") || q.has("beat");
  if (!forced) {
    try { sessionStorage.setItem("lpIntroSeen", "1"); } catch (_) {}
  }

  const hero = document.getElementById("hero");
  const stage = hero.querySelector(".lpi");
  const canvas = stage.querySelector(".lpi-canvas");
  const $ = (sel) => canvas.querySelector(sel);
  const $$ = (sel) => [...canvas.querySelectorAll(sel)];
  const heroParts = [...hero.children].filter((el) => el !== stage && el.tagName !== "SCRIPT");

  // Scale the fixed 880×500 canvas to fit the hero, like a video frame.
  const fit = () => {
    const s = Math.min(1, (hero.clientWidth - 32) / 880, hero.clientHeight / 500);
    canvas.style.transform = `translate(-50%, -50%) scale(${s})`;
  };
  fit();
  addEventListener("resize", fit);

  const cap = (n) => $(`[data-cap="${n}"]`);
  const show = { autoAlpha: 1, duration: 0.6, ease: "power2.out" };
  const hide = { autoAlpha: 0, duration: 0.5, ease: "power2.in" };

  // Split each caption into words so the title can be revealed word by word.
  // A "|" in a caption breaks the line; the part before it is set soft.
  $$(".lpi-caption").forEach((c) => {
    const parts = c.textContent.trim().split("|");
    c.innerHTML = parts.map((part, i) => part.trim().split(/\s+/)
      .map((w) => `<span class="lpi-word${parts.length > 1 && i === 0 ? " lpi-soft" : ""}">${w}</span>`)
      .join(" ")).join("<br>");
  });

  const tl = gsap.timeline({ paused: true, onComplete: finish });

  // Every beat has the same rhythm, on Ken's feedback ("read the sentence
  // first, then show the picture, and not fast"):
  //   1. the title appears alone, big and centred, word by word
  //   2. it holds long enough to read
  //   3. it rises to the top, and only then does the picture come in
  // titleIn() plays steps 1–3 starting at `at` and returns when the picture
  // may start. Retime the whole intro by changing these numbers.
  const WORD = 0.16;      // gap between words
  const READ = 1.4;       // hold once the last word is in
  const RISE = 0.7;       // title moving up to the top
  const titleIn = (n, at) => {
    const c = cap(n);
    const words = c.querySelectorAll(".lpi-word");
    // Big while it's read alone, but never wider than the canvas.
    const first = words[0], last = words[words.length - 1];
    const textWidth = last.offsetLeft + last.offsetWidth - first.offsetLeft;
    tl.set(c, { autoAlpha: 1, y: 215, scale: Math.min(1.3, 820 / textWidth) }, at);
    tl.fromTo(words,
      { opacity: 0, y: 16, filter: "blur(8px)" },
      { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.8, ease: "power3.out", stagger: WORD },
      at);
    const rise = at + 0.8 + WORD * (words.length - 1) + READ;
    tl.to(c, { y: 0, scale: 1, duration: RISE, ease: "power3.inOut" }, rise);
    return rise + RISE * 0.6; // the picture starts as the title settles
  };

  let t = 0;
  const beatStart = [];
  const beat = (n) => { beatStart[n] = t; tl.addLabel(`b${n}`, t); };
  const hold = (n, secs) => { tl.addLabel(`b${n}hold`, t + secs * 0.5); t += secs; };

  // Pictures move briskly; only the titles take their time (Ken: "the text
  // animation is perfect … the picture is too long").

  // ── Beat 1: the paper mountain ───────────────────────────────────
  beat(1);
  t = titleIn(1, 0.3);
  const pile = [
    [-215, -25, -14], [-150, 45, 8], [-65, -55, -5], [30, 35, 12], [125, -45, -9],
    [210, 25, 6], [-110, -5, 18], [75, -5, -16], [160, 65, 3], [-25, 75, -8], [0, 5, 4],
  ];
  const docs = $$(".lpi-doc");
  docs.forEach((d, i) => {
    const [x, y, r] = pile[i % pile.length];
    tl.fromTo(d,
      // Short drop from just above, so papers never cross the title.
      { autoAlpha: 0, x, y: y - 70, rotation: r * 2, scale: 1.12 },
      { autoAlpha: 1, x, y, rotation: r, scale: 1, duration: 0.5, ease: "back.out(1.3)" },
      t + i * 0.08);
  });
  t += docs.length * 0.08 + 0.5;
  hold(1, 0.9);
  tl.to([cap(1), ...docs], hide, t);
  t += 0.5;

  // ── Beat 2: drop it all in ───────────────────────────────────────
  beat(2);
  t = titleIn(2, t);
  tl.fromTo($(".lpi-library"), { autoAlpha: 0, scale: 0.94 }, { ...show, scale: 1 }, t);
  t += 0.3;
  // The same papers rain into the window, then each row gets read.
  tl.fromTo(docs,
    { autoAlpha: 0, x: (i) => -200 + i * 40, y: -240, rotation: (i) => (i % 2 ? 10 : -10), scale: 0.6 },
    { autoAlpha: 1, x: 0, y: 30, rotation: 0, scale: 0.3, duration: 0.45, ease: "power2.in", stagger: 0.04, immediateRender: false },
    t);
  docs.forEach((d, i) => tl.set(d, { autoAlpha: 0 }, t + 0.45 + i * 0.04));
  const count = { n: 0 };
  const rows = $$(".lpi-row");
  const ROW = 0.22;
  tl.to(count, {
    n: 128, duration: 0.3 + rows.length * ROW, ease: "power1.inOut",
    onUpdate: () => { $(".lpi-count").textContent = Math.round(count.n); },
  }, t + 0.2);
  rows.forEach((row, i) => {
    const at = t + 0.2 + i * ROW;
    tl.to(row.querySelector(".lpi-scan-fill"), { scaleX: 1, duration: 0.25, ease: "none" }, at);
    tl.to(row.querySelector(".lpi-scan"), { opacity: 0, duration: 0.1 }, at + 0.25);
    tl.fromTo(row.querySelector(".lpi-check"), { opacity: 0, scale: 0.4 },
      { opacity: 1, scale: 1, duration: 0.25, ease: "back.out(2)" }, at + 0.25);
  });
  t += 0.2 + rows.length * ROW + 0.3;
  hold(2, 0.8);
  tl.to([cap(2), $(".lpi-library")], hide, t);
  t += 0.5;

  // ── Beat 3: just ask — question, then answer, then its source ────
  beat(3);
  t = titleIn(3, t);
  tl.fromTo($(".lpi-chat"), { autoAlpha: 0, y: 16 }, { ...show, y: 0 }, t);
  t += 0.4;
  tl.fromTo($(".lpi-q"), { opacity: 0, y: 8, scale: 0.97 },
    { opacity: 1, y: 0, scale: 1, duration: 0.4, ease: "power2.out" }, t);
  t += 0.8;
  tl.fromTo($(".lpi-a"), { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.45, ease: "power2.out" }, t);
  t += 1.2; // the answer is the point: give it a moment before its source
  tl.fromTo($(".lpi-paper:not(.lpi-report)"), { autoAlpha: 0, x: 30 }, { ...show, x: 0 }, t);
  t += 0.4;
  tl.to($(".lpi-hl-bg"), { scaleX: 1, duration: 0.45, ease: "power2.inOut" }, t);
  tl.fromTo($(".lpi-source"), { scale: 1 }, { scale: 1.08, duration: 0.2, yoyo: true, repeat: 1 }, t);
  t += 0.45;
  hold(3, 1.5);
  tl.to([cap(3), $(".lpi-chat"), $(".lpi-paper:not(.lpi-report)")], hide, t);
  t += 0.5;

  // ── Beat 4: your team + the final report ─────────────────────────
  beat(4);
  t = titleIn(4, t);
  tl.fromTo($(".lpi-feed"), { autoAlpha: 0, y: 16 }, { ...show, y: 0 }, t);
  tl.fromTo($(".lpi-report"), { autoAlpha: 0, x: 30 }, { ...show, x: 0 }, t + 0.15);
  t += 0.4;
  const reportRows = $$(".lpi-report-row");
  const sales = $$(".lpi-sale");
  const SALE = 0.4;
  sales.forEach((sale, i) => {
    const at = t + i * SALE;
    tl.fromTo(sale, { opacity: 0, x: -14 }, { opacity: 1, x: 0, duration: 0.35, ease: "power2.out" }, at);
    tl.fromTo(reportRows[i], { opacity: 0, x: -10 }, { opacity: 1, x: 0, duration: 0.35, ease: "power2.out" }, at + 0.15);
  });
  const total = { n: 0 };
  tl.to(total, {
    n: 10100, duration: sales.length * SALE, ease: "power1.out",
    onUpdate: () => { $(".lpi-total").textContent = `${Math.round(total.n).toLocaleString("fr-CA")} $`; },
  }, t + 0.15);
  t += sales.length * SALE + 0.3;
  hold(4, 1.3);
  tl.to([cap(4), $(".lpi-feed"), $(".lpi-report")], { ...hide, y: -10 }, t);
  t += 0.6;

  // ── Beat 5: the closing line — why this exists ───────────────────
  // Stays centred (no rise, no picture) and holds longer than the others.
  beat(5);
  const closing = cap(5);
  const closingWords = closing.querySelectorAll(".lpi-word");
  tl.set(closing, { autoAlpha: 1, y: 195 }, t);
  tl.fromTo(closingWords,
    { opacity: 0, y: 16, filter: "blur(8px)" },
    { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.9, ease: "power3.out", stagger: 0.2 }, t);
  t += 0.9 + 0.2 * (closingWords.length - 1) + 2.2;
  tl.addLabel("b5hold", t - 1);
  tl.to(closing, { ...hide, duration: 0.6 }, t);
  t += 0.7;

  // ── Beat 6: land on the real hero ────────────────────────────────
  beat(6);
  tl.fromTo(heroParts, { opacity: 0, y: 14 },
    { opacity: 1, y: 0, duration: 0.8, ease: "power2.out", stagger: 0.12 }, t);
  tl.addLabel("b6hold", t + 0.8 + 0.12 * heroParts.length);

  function finish() {
    if (q.has("t") || q.has("beat")) return; // keep the frozen frame inspectable
    gsap.set(heroParts, { clearProps: "all" });
    root.classList.remove("lpi-on");
    stage.remove();
  }

  stage.querySelector(".lpi-skip").addEventListener("click", () => tl.progress(1, false));

  // ── Draft tools ──────────────────────────────────────────────────
  window.lpIntro = tl;
  if (q.get("intro") === "debug" || q.has("t") || q.has("beat")) {
    const badge = document.createElement("div");
    badge.className = "lpi-debug";
    document.body.append(badge);
    const holds = [1, 2, 3, 4, 5, 6].map((b) => tl.labels[`b${b}hold`]);
    const render = () => {
      const t = tl.time();
      const beat = beatStart.filter((s) => s !== undefined && t >= s).length;
      badge.textContent = `Beat ${beat} · ${t.toFixed(1)}s / ${tl.duration().toFixed(1)}s`;
    };
    tl.eventCallback("onUpdate", render);
    if (q.has("beat")) return tl.seek(holds[Number(q.get("beat")) - 1] ?? 0, false).pause(), render();
    if (q.has("t")) return tl.seek(parseFloat(q.get("t")) || 0, false).pause(), render();
  }
  tl.play();
})();
