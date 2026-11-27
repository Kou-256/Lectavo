const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

// Header rule once the page scrolls.
const header = document.querySelector("[data-header]");
if (header) {
  const update = () => header.classList.toggle("is-scrolled", window.scrollY > 8);
  update();
  window.addEventListener("scroll", update, { passive: true });
}

// PDF translation explainer: four steps played in order while it is in view.
const explainer = document.querySelector("[data-explainer]");
if (explainer) {
  const stage = explainer.querySelector(".explainer-stage");
  const tabs = [...explainer.querySelectorAll("[data-step]")];
  const status = explainer.querySelector("[data-stage-status]");
  const toggle = explainer.querySelector("[data-toggle]");
  const messages = [
    "文字のかたまりと、訳さない場所を見つけています",
    "化学式・記号・引用番号に目印を付けています",
    "段落ごとに日本語へ翻訳しています",
    "訳文を元の枠に書き戻しました",
  ];
  const durations = [2600, 2600, 3800, 3400];
  let step = 0;
  let playing = !reducedMotion.matches;
  let visible = false;
  let started = 0;
  let elapsed = 0;
  let frame = 0;

  // Paragraphs are translated one after another, top to bottom.
  explainer.querySelectorAll("[data-block]").forEach((block, index) => {
    block.style.setProperty("--delay", `${index * 0.42}s`);
  });

  function show(next) {
    step = next;
    stage.dataset.stage = String(step);
    status.textContent = messages[step];
    tabs.forEach((tab, index) => {
      tab.setAttribute("aria-selected", String(index === step));
      tab.tabIndex = index === step ? 0 : -1;
      tab.style.setProperty("--progress", index === step && !playing ? 1 : 0);
    });
    elapsed = 0;
    started = performance.now();
  }

  function tick(now) {
    frame = 0;
    if (!playing || !visible) return;
    const spent = elapsed + (now - started);
    const progress = Math.min(1, spent / durations[step]);
    tabs[step].style.setProperty("--progress", progress);
    if (progress >= 1) show((step + 1) % tabs.length);
    frame = requestAnimationFrame(tick);
  }

  function run() {
    if (!frame && playing && visible) {
      started = performance.now();
      frame = requestAnimationFrame(tick);
    }
  }

  function pause() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    elapsed += performance.now() - started;
  }

  function setPlaying(value) {
    playing = value;
    toggle.classList.toggle("is-paused", !playing);
    toggle.setAttribute("aria-label", playing ? "アニメーションを一時停止" : "アニメーションを再生");
    if (playing) run();
    else pause();
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => {
      setPlaying(false);
      show(index);
    });
    tab.addEventListener("keydown", (event) => {
      const moves = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
      if (!(event.key in moves)) return;
      event.preventDefault();
      const next = (index + moves[event.key] + tabs.length) % tabs.length;
      setPlaying(false);
      show(next);
      tabs[next].focus();
    });
  });

  toggle.addEventListener("click", () => setPlaying(!playing));

  if (reducedMotion.matches) {
    // Without motion the finished page is shown; the steps stay clickable.
    setPlaying(false);
    show(3);
  } else {
    show(0);
    const observer = new IntersectionObserver(
      ([entry]) => {
        visible = entry.isIntersecting;
        if (visible) run();
        else pause();
      },
      { threshold: 0.35 },
    );
    observer.observe(stage);
  }
}
