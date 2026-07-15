const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
document.documentElement.classList.add("motion-ready");

const revealElements = document.querySelectorAll(".reveal");

if (reducedMotion.matches || !("IntersectionObserver" in window)) {
  revealElements.forEach((element) => element.classList.add("is-visible"));
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { threshold: 0.14 },
  );

  revealElements.forEach((element) => observer.observe(element));
}

const flowNodes = [...document.querySelectorAll("[data-flow-step]")];
const flowStatus = document.querySelector("[data-flow-status]");
const flowMessages = [
  "動画を読み込んでいます",
  "スライドの変化を検出しています",
  "重複スライドを照合しています",
  "CUDAで音声を文字起こししています",
  "PDFと講義ノートを生成しました",
];

let activeFlowStep = 0;

function updateFlow() {
  flowNodes.forEach((node, index) => {
    node.classList.toggle("is-active", index === activeFlowStep);
  });
  if (flowStatus) flowStatus.textContent = flowMessages[activeFlowStep];
  activeFlowStep = (activeFlowStep + 1) % flowNodes.length;
}

if (!reducedMotion.matches && flowNodes.length > 0) {
  window.setInterval(updateFlow, 1700);
}
