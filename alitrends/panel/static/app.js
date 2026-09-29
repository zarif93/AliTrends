"use strict";

const CSRF = document.querySelector('meta[name="csrf"]')?.content || "";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Forms and buttons that ask before doing something drastic.
document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

// --- jobs: preview / post now -----------------------------------------------

function renderResult(kind, result, output) {
  output.replaceChildren();
  if (kind === "preview") {
    const product = result.product;
    const facts = [
      `${product.price} ${product.currency}`,
      product.discount ? `-${product.discount}%` : null,
      product.rating ? `⭐ ${product.rating}` : null,
      `${product.sales} מכירות`,
      product.hot ? "🔥 מוצר חם" : null,
      `עמלה ${product.commission}%`,
    ].filter(Boolean).join(" · ");
    const posts = el("div", { class: "preview-posts" },
      ...result.posts.map((post) => el("div", { class: "preview-post" }, el("h4", {}, post.label), el("div", { dir: "auto" }, post.text))));
    output.append(el("div", { class: "preview" },
      el("div", {}, el("img", { src: product.image, alt: "" })),
      el("div", {},
        el("p", {}, el("a", { href: product.link, target: "_blank", rel: "noopener noreferrer" }, product.title)),
        el("p", { class: "small muted" }, facts),
        posts)));
    return;
  }
  const errors = (result.errors || []).length ? ` (${result.errors.length} שגיאות: ${result.errors.join("; ")})` : "";
  output.append(el("div", { class: "flash ok" }, `✅ פורסם ל-${result.published} יעדים${errors}`));
}

async function runJob(button) {
  const output = document.querySelector(button.dataset.output);
  if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
  const buttons = document.querySelectorAll("[data-job]");
  buttons.forEach((b) => (b.disabled = true));
  const status = el("p", { class: "muted" }, el("span", { class: "spinner" }), "נשלח לבוט…");
  output.replaceChildren(status);
  try {
    const response = await fetch(button.dataset.job, { method: "POST", headers: { "X-CSRF-Token": CSRF } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { job_id: jobId } = await response.json();
    const started = Date.now();
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const job = await (await fetch(`/jobs/${jobId}.json`)).json();
      if (job.status === "done") { renderResult(job.kind, job.result, output); break; }
      if (job.status === "failed" || job.status === "cancelled") {
        output.replaceChildren(el("div", { class: "flash error" }, `❌ ${job.result?.error || job.status_label}`));
        break;
      }
      const seconds = Math.round((Date.now() - started) / 1000);
      const hint = !job.bot_alive ? " הבוט לא מגיב — בדקו שהשירות רץ." :
        seconds > 60 ? " (בחירת מוצר ראשונה יכולה לקחת עד דקה או שתיים)" : "";
      status.replaceChildren(el("span", { class: "spinner" }), `${job.status_label}… ${seconds} שניות.${hint}`);
    }
  } catch (error) {
    output.replaceChildren(el("div", { class: "flash error" }, `שגיאה: ${error.message}`));
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

document.querySelectorAll("[data-job]").forEach((button) => {
  button.addEventListener("click", () => runJob(button));
});

// --- add-target form: hint and token field per platform ----------------------

document.querySelectorAll("[data-platform-select]").forEach((select) => {
  const form = select.closest("form");
  const hint = form.querySelector("[data-platform-hint]");
  const secret = form.querySelector("[data-secret-field]");
  const targetId = form.querySelector("[data-target-id]");
  const update = () => {
    const option = select.selectedOptions[0];
    hint.textContent = option.dataset.hint;
    const needsSecret = Boolean(option.dataset.secret);
    secret.hidden = !needsSecret;
    targetId.placeholder = needsSecret ? "(יתמלא אוטומטית)" : "";
  };
  select.addEventListener("change", update);
  update();
});

// --- charts ------------------------------------------------------------------

const PALETTE = ["#2a9df4", "#3b5bdb", "#d6336c", "#495057", "#e8590c", "#2f9e44", "#ae3ec9", "#f59f00"];
const PLATFORM_COLORS = { "טלגרם": "#2a9df4", "פייסבוק": "#3b5bdb", "אינסטגרם": "#d6336c", "Threads": "#868e96" };

function drawCharts() {
  if (!window.Chart) return;
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  Chart.defaults.color = dark ? "#98a0b3" : "#6b7385";
  Chart.defaults.borderColor = dark ? "#2b3140" : "#e1e4ea";
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

  document.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
    const data = JSON.parse(document.getElementById(canvas.dataset.source).textContent);
    if (canvas.dataset.chart === "bar") {
      new Chart(canvas, {
        type: "bar",
        data: {
          labels: data.labels,
          datasets: data.series.map((s, i) => ({
            label: s.name, data: s.values, backgroundColor: PLATFORM_COLORS[s.name] || PALETTE[i % PALETTE.length],
            borderRadius: 4, maxBarThickness: 28,
          })),
        },
        options: { maintainAspectRatio: false, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
                   plugins: { legend: { position: "bottom" } } },
      });
    } else {
      new Chart(canvas, {
        type: "line",
        data: {
          labels: data.labels,
          datasets: data.series.map((s, i) => ({
            label: s.name, data: data.labels.map((d) => s.points[d] ?? null), spanGaps: true, tension: .25,
            borderColor: PALETTE[i % PALETTE.length], backgroundColor: PALETTE[i % PALETTE.length], pointRadius: 2,
          })),
        },
        options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } },
      });
    }
  });
}

if (document.readyState === "complete") drawCharts();
else window.addEventListener("load", drawCharts);
