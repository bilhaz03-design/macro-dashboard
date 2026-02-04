const DATA_URL = "data.json";
let currentLang = "en";
let allCountries = [];

const statusClass = {
  Bra: "status-good",
  Good: "status-good",
  Neutral: "status-neutral",
  Svag: "status-weak",
  Weak: "status-weak",
  "Ser bra ut": "status-good",
  Healthy: "status-good",
  "Går starkt": "status-good",
  Strong: "status-good",
  "Neutral / saktar till": "status-neutral",
  "Neutral / slowing": "status-neutral",
  Oroligt: "status-weak",
  Watch: "status-weak",
  Dåligt: "status-weak",
};

const i18n = {
  en: {
    eyebrow: "Macro Health Dashboard",
    title: "Signal-to-Confirmation Tracker",
    subhead:
      "Advanced, clean, and readable. Built for weekly updates and fast decision context.",
    excelLink: "Open Excel Dashboard",
    updateButton: "Update Data",
    updateCopied: "Update command copied. Run in Terminal.",
    lastUpdated: "Last updated",
    executiveSummary: "Executive Summary",
    globalRisk: "Global Risk Filter",
    notes: "Notes",
    signalScore: "Signal Score",
    systemStatus: "System Status",
    updatedWeekly: "Updated weekly",
    compositeMomentum: "Composite momentum",
    countries: {
      Sweden: "Sweden",
      China: "China",
    },
    labels: {
      signalTimeline: "Signal timeline",
    },
    notesList: [
      "Focus on 3-month direction over single prints.",
      "Earlier signals lead, hard data confirms.",
      "Status labels are derived from block majority.",
    ],
  },
  sv: {
    eyebrow: "Makrohälsa Dashboard",
    title: "Signal‑till‑Bekräftelse",
    subhead:
      "Avancerad, ren och lättläst. Byggd för veckouppdateringar och snabb beslutsöverblick.",
    excelLink: "Öppna Excel‑dashboard",
    updateButton: "Uppdatera data",
    updateCopied: "Uppdateringskommando kopierat. Kör i Terminal.",
    lastUpdated: "Senast uppdaterad",
    executiveSummary: "Sammanfattning",
    globalRisk: "Global riskfilter",
    notes: "Noteringar",
    signalScore: "Signalpoäng",
    systemStatus: "Systemstatus",
    updatedWeekly: "Uppdateras veckovis",
    compositeMomentum: "Samlad momentum",
    countries: {
      Sweden: "Sverige",
      China: "Kina",
    },
    labels: {
      signalTimeline: "Signal-tidslinje",
    },
    notesList: [
      "Fokusera på 3‑månaders riktning snarare än enskilda utfall.",
      "Tidiga signaler leder, hård data bekräftar.",
      "Status bygger på majoriteten av blocken.",
    ],
  },
};

function setStatusBadge(el, label) {
  el.textContent = label;
  const cls = statusClass[label] || "status-neutral";
  el.classList.add(cls);
}

function getField(obj, key) {
  if (!obj) return "";
  const svKey = `${key}_sv`;
  if (currentLang === "sv" && obj[svKey]) {
    return obj[svKey];
  }
  return obj[key] ?? "";
}

function renderSummary(summary) {
  const template = document.getElementById("summary-template");
  const container = document.getElementById("summary");
  container.innerHTML = "";
  summary.forEach((item) => {
    const node = template.content.cloneNode(true);
    node.querySelector(".summary-title").textContent =
      i18n[currentLang].countries[item.title] || item.title;
    node.querySelector(".summary-value").textContent = getField(item, "value");
    node.querySelector(".summary-note").textContent = getField(item, "note");
    const tag = node.querySelector(".summary-tag");
    setStatusBadge(tag, getField(item, "status"));
    if (item.sources && item.sources.length) {
      const note = node.querySelector(".summary-note");
      const sourceWrap = document.createElement("div");
      sourceWrap.classList.add("block-sources");
      item.sources.forEach((source) => {
        const link = document.createElement("a");
        link.href = source.url;
        link.textContent = source.label;
        link.target = "_blank";
        link.rel = "noreferrer";
        sourceWrap.appendChild(link);
      });
      note.appendChild(sourceWrap);
    }
    container.appendChild(node);
  });
}

function renderCountry(country) {
  const template = document.getElementById("country-template");
  const blockTemplate = document.getElementById("block-template");
  const node = template.content.cloneNode(true);

  node.querySelector(".country-name").textContent =
    i18n[currentLang].countries[country.name] || country.name;
  node.querySelector(".country-sub").textContent = getField(country, "subtitle");

  const statusTag = node.querySelector(".status-tag");
  setStatusBadge(statusTag, getField(country, "overall"));

  const grid = node.querySelector(".block-grid");
  country.blocks.forEach((block) => {
    const blockNode = blockTemplate.content.cloneNode(true);
    blockNode.querySelector(".block-title").textContent = getField(block, "label");
    const statusNode = blockNode.querySelector(".block-status");
    setStatusBadge(statusNode, getField(block, "status"));
    const detail = blockNode.querySelector(".block-detail");
    detail.textContent = getField(block, "detail");
    const sparkTarget = blockNode.querySelector(".block-sparkline");
    if (block.trend && block.trend.length) {
      const svg = renderSparkline(block.trend);
      sparkTarget.appendChild(svg);
    } else {
      sparkTarget.textContent = "—";
    }
    if (block.sources && block.sources.length) {
      const sourceWrap = document.createElement("div");
      sourceWrap.classList.add("block-sources");
      block.sources.forEach((source) => {
        const link = document.createElement("a");
        link.href = source.url;
        link.textContent = source.label;
        link.target = "_blank";
        link.rel = "noreferrer";
        sourceWrap.appendChild(link);
      });
      detail.appendChild(sourceWrap);
    }
    grid.appendChild(blockNode);
  });

  const timeline = node.querySelector(".timeline");
  const timelineData = currentLang === "sv" && country.timeline_sv ? country.timeline_sv : country.timeline;
  timelineData.forEach((item) => {
    const span = document.createElement("span");
    span.textContent = item;
    timeline.appendChild(span);
  });

  const label = node.querySelector(".label");
  label.textContent = i18n[currentLang].labels.signalTimeline;

  return node;
}

async function loadData() {
  try {
    const response = await fetch(DATA_URL);
    if (!response.ok) {
      throw new Error("Failed to load data.json");
    }
    return response.json();
  } catch (err) {
    const inline = document.getElementById("inline-data");
    if (inline && inline.textContent.trim()) {
      return JSON.parse(inline.textContent);
    }
    throw err;
  }
}

function renderDashboard(data) {
  const lastUpdated = document.getElementById("last-updated");
  lastUpdated.textContent = `${i18n[currentLang].lastUpdated}: ${data.last_updated}`;

  const signalScore = document.getElementById("signal-score");
  signalScore.textContent = data.signal_score?.value ?? "--";
  const signalScoreNote = document.getElementById("signal-score-note");
  signalScoreNote.textContent = currentLang === "sv"
    ? i18n[currentLang].compositeMomentum
    : data.signal_score?.note ?? "";

  const systemStatus = document.getElementById("system-status");
  systemStatus.textContent = getField(data.system_status, "label") ?? "--";
  const systemStatusNote = document.getElementById("system-status-note");
  systemStatusNote.textContent = currentLang === "sv"
    ? i18n[currentLang].updatedWeekly
    : getField(data.system_status, "note") ?? "";

  const globalRisk = document.getElementById("global-risk");
  setStatusBadge(globalRisk, data.global_risk.status);
  const globalRiskNote = document.getElementById("global-risk-note");
  globalRiskNote.textContent = getField(data.global_risk, "note");
  const globalRiskSources = document.getElementById("global-risk-sources");
  globalRiskSources.innerHTML = "";
  if (data.global_risk.sources && data.global_risk.sources.length) {
    data.global_risk.sources.forEach((source) => {
      const link = document.createElement("a");
      link.href = source.url;
      link.textContent = source.label;
      link.target = "_blank";
      link.rel = "noreferrer";
      globalRiskSources.appendChild(link);
    });
  }

  const summaryBadge = document.getElementById("summary-badge");
  if (summaryBadge && data.system_status?.label) {
    setStatusBadge(summaryBadge, getField(data.system_status, "label"));
  }
  const summaryText = document.getElementById("summary-text");
  if (summaryText && data.system_status?.summary) {
    summaryText.textContent = getField(data.system_status, "summary");
  }

  renderSummary(data.summary);

  const grid = document.getElementById("country-grid");
  grid.innerHTML = "";
  const active = grid.dataset.activeCountry || "Sweden";
  data.countries
    .filter((country) => country.name === active)
    .forEach((country) => {
      grid.appendChild(renderCountry(country));
    });
}

function setupTabs() {
  const buttons = document.querySelectorAll(".tab-button");
  const grid = document.getElementById("country-grid");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      grid.dataset.activeCountry = btn.dataset.country;
      renderDashboard({ ...window.__dashboardData });
    });
  });
}

function setupLanguageToggle() {
  const toggle = document.getElementById("lang-toggle");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    currentLang = currentLang === "en" ? "sv" : "en";
    toggle.setAttribute("aria-pressed", currentLang === "sv" ? "true" : "false");
    toggle.textContent = currentLang === "sv" ? "English" : "Svenska";
    applyStaticLabels();
    renderDashboard({ ...window.__dashboardData });
  });
}

function applyStaticLabels() {
  document.querySelectorAll(".hero-label")[0].textContent = i18n[currentLang].signalScore;
  document.querySelectorAll(".hero-label")[1].textContent = i18n[currentLang].systemStatus;
  document.querySelectorAll(".hero-sub")[0].textContent = i18n[currentLang].compositeMomentum;
  document.querySelectorAll(".hero-sub")[1].textContent = i18n[currentLang].updatedWeekly;
  document.getElementById("label-exec-summary").textContent = i18n[currentLang].executiveSummary;
  document.getElementById("label-global-risk").textContent = i18n[currentLang].globalRisk;
  document.getElementById("label-notes").textContent = i18n[currentLang].notes;
  document.querySelectorAll(".tab-button")[0].textContent = i18n[currentLang].countries.Sweden;
  document.querySelectorAll(".tab-button")[1].textContent = i18n[currentLang].countries.China;
  document.getElementById("label-eyebrow").textContent = i18n[currentLang].eyebrow;
  document.getElementById("label-title").textContent = i18n[currentLang].title;
  document.getElementById("label-subhead").textContent = i18n[currentLang].subhead;
  document.getElementById("label-excel-link").textContent = i18n[currentLang].excelLink;
  const updateButton = document.getElementById("update-button");
  if (updateButton) updateButton.textContent = i18n[currentLang].updateButton;
  document.getElementById("note-1").textContent = i18n[currentLang].notesList[0];
  document.getElementById("note-2").textContent = i18n[currentLang].notesList[1];
  document.getElementById("note-3").textContent = i18n[currentLang].notesList[2];
}

function setupUpdateButton() {
  const button = document.getElementById("update-button");
  if (!button) return;
  const cmd =
    "/Users/bobbo/Desktop/Finans\\ Projects/.venv/bin/python /Users/bobbo/Desktop/Finans\\ Projects/scripts/update_dashboard.py";
  button.addEventListener("click", async () => {
    try {
      const response = await fetch("/update", { method: "POST" });
      if (response.ok) {
        button.textContent = currentLang === "sv" ? "Uppdaterad" : "Updated";
        setTimeout(() => {
          button.textContent = i18n[currentLang].updateButton;
        }, 2000);
        return;
      }
    } catch (_err) {
      // fall back to clipboard
    }
    try {
      await navigator.clipboard.writeText(cmd);
      button.textContent = i18n[currentLang].updateCopied;
      setTimeout(() => {
        button.textContent = i18n[currentLang].updateButton;
      }, 2000);
    } catch (err) {
      alert(cmd);
    }
  });
}

loadData()
  .then((data) => {
    window.__dashboardData = data;
    allCountries = data.countries || [];
    applyStaticLabels();
    setupTabs();
    setupLanguageToggle();
    setupUpdateButton();
    renderDashboard(data);
  })
  .catch((err) => {
    console.error(err);
    const grid = document.getElementById("country-grid");
    grid.innerHTML = `<div class="panel">Failed to load data. ${err.message}</div>`;
  });

function renderSparkline(values) {
  const width = 120;
  const height = 36;
  const padding = 4;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const points = values
    .map((v, i) => {
      const x = padding + (i / (values.length - 1)) * (width - padding * 2);
      const y = height - padding - ((v - min) / range) * (height - padding * 2);
      return `${x},${y}`;
    })
    .join(" ");

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "block-sparkline");

  const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  polyline.setAttribute("fill", "none");
  polyline.setAttribute("stroke", "#0e7c86");
  polyline.setAttribute("stroke-width", "2");
  polyline.setAttribute("points", points);

  svg.appendChild(polyline);
  return svg;
}
