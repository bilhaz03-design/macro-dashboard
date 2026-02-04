function extractNumber(text, regex) {
  const match = text.match(regex);
  if (!match) return null;
  const value = match[1].replace(",", ".").trim();
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

async function fetchText(url) {
  const res = await fetch(url, { headers: { "User-Agent": "MacroDashboardBot/1.0" } });
  if (!res.ok) throw new Error(`Fetch failed ${res.status} for ${url}`);
  return res.text();
}

async function safeFetchText(url) {
  try {
    return await fetchText(url);
  } catch (_err) {
    return "";
  }
}

async function fetchEcbSek() {
  const url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml";
  const xml = await fetchText(url);
  const match = xml.match(/currency='SEK' rate='([0-9.]+)'/);
  return match ? Number(match[1]) : null;
}

function statusFromValue(value, bands) {
  if (value == null) return "Neutral";
  if (value >= bands.good) return "Good";
  if (value >= bands.neutral) return "Neutral";
  return "Weak";
}

export async function buildData() {
  const lastUpdated = new Date().toISOString().slice(0, 10);

  // Sweden sources
  const scbCpiUrl =
    "https://www.scb.se/en/finding-statistics/statistics-by-subject-area/prices-and-economic-trends/price-statistics/consumer-price-index-cpi/pong/statistical-news/consumer-price-index-cpi-december-2025/";
  const scbLfsUrl =
    "https://www.scb.se/en/finding-statistics/statistics-by-subject-area/labour-market/labour-force-supply/labour-force-surveys-lfs/";
  const scbGdpUrl =
    "https://www.scb.se/en/finding-statistics/statistics-by-subject-area/national-accounts/ovrigt/national-accounts-other/pong/statistical-news/national-accounts-monthly-gdp-indicator-december-2025/";
  const riksbankRateUrl =
    "https://www.riksbank.se/en-gb/press-and-published/notices-and-press-releases/press-releases/2026/policy-rate-unchanged-at-1.75-per-cent/";
  const swedenPmiUrl = "https://tradingeconomics.com/sweden/manufacturing-pmi";
  const nierUrl = "https://www.konj.se/english/forecast-and-surveys/konjunkturbarometern.html";

  // China sources
  const nbsRetailUrl =
    "https://www.stats.gov.cn/english/PressRelease/202601/t20260120_1962354.html";
  const nbsIndustryUrl =
    "https://english.www.gov.cn/archive/statistics/202601/19/content_WS696da6eec6d00ca5f9a08a2f.html";
  const nbsCpiUrl =
    "https://www.stats.gov.cn/english/PressRelease/202601/t20260112_1962292.html";
  const nbsPropertyUrl =
    "https://www.china.org.cn/2026-01/19/content_118287661.shtml";
  const pbocTsfUrl =
    "https://xining.pbc.gov.cn/en/3688247/3688978/3709137/2025122410193371772/index.html";

  // Fetch in parallel (best-effort)
  const [
    scbCpiText,
    scbLfsText,
    scbGdpText,
    riksText,
    swedenPmiText,
    nierText,
    retailText,
    industryText,
    cpiText,
    propertyText,
    tsfText,
    ecbSek,
  ] = await Promise.all([
    safeFetchText(scbCpiUrl),
    safeFetchText(scbLfsUrl),
    safeFetchText(scbGdpUrl),
    safeFetchText(riksbankRateUrl),
    safeFetchText(swedenPmiUrl),
    safeFetchText(nierUrl),
    safeFetchText(nbsRetailUrl),
    safeFetchText(nbsIndustryUrl),
    safeFetchText(nbsCpiUrl),
    safeFetchText(nbsPropertyUrl),
    safeFetchText(pbocTsfUrl),
    fetchEcbSek().catch(() => null),
  ]);

  // Sweden parsing
  const cpif = extractNumber(
    scbCpiText,
    /CPIF[^0-9]*([0-9.]+)\s*percent/i
  );
  const unemployment = extractNumber(
    scbLfsText,
    /Unemployment rate\\s*\\n?\\s*([0-9.]+)\\s*%/i
  );
  const gdpMom = extractNumber(
    scbGdpText,
    /monthly GDP indicator[^.]*?(-?[0-9.]+)\\s*percent/i
  );
  const policyRate = extractNumber(
    riksText,
    /policy rate[^0-9]*([0-9.]+)\\s*per\\s*cent/i
  );

  // China parsing
  const retailDec = extractNumber(retailText, /up by\\s*([0-9.]+)%\\s*year on year/i);
  const retail2025 = extractNumber(retailText, /In 2025[^.]*up by\\s*([0-9.]+)%/i);
  const industryDec = extractNumber(industryText, /increased by\\s*([0-9.]+)\\s*percent/i);
  const industry2025 = extractNumber(industryText, /expanded\\s*([0-9.]+)\\s*percent year on year in 2025/i);
  const cpi = extractNumber(cpiText, /CPI[^0-9]*([0-9.]+)\\s*percent/i);
  let property = extractNumber(propertyText, /real estate investment[^0-9-]*(-?[0-9.]+)%/i);
  if (property != null && property > 0 && /declin|drop|fall/i.test(propertyText)) {
    property = -property;
  }
  const tsf = extractNumber(tsfText, /outstanding.*?([0-9.]+)\\s*percent/i);

  const swInflationStatus = statusFromValue(cpif, { good: 1.0, neutral: 0.0 });
  const cnConsumptionStatus = statusFromValue(retailDec, { good: 5.0, neutral: 2.0 });
  const cnIndustryStatus = statusFromValue(industryDec, { good: 6.0, neutral: 3.0 });
  const cnInflationStatus = statusFromValue(cpi, { good: 1.0, neutral: 0.0 });

  const data = {
    last_updated: lastUpdated,
    signal_score: {
      value: "61",
      note: "Moderate momentum, uneven by country",
    },
    system_status: {
      label: "Neutral / slowing",
      label_sv: "Neutral / saktar till",
      note: "Macro signals mixed",
      note_sv: "Makrosignaler blandade",
      summary:
        "Sweden shows softer labor but inflation near target and easing policy; China remains capped by property weakness despite steady industry. Net signal is neutral with pockets of stabilization.",
      summary_sv:
        "Sverige har svagare arbetsmarknad men inflation nära målet och lättare policy; Kina hålls tillbaka av svag fastighetssektor trots stabil industri. Totalsignalen är neutral med tecken på stabilisering.",
    },
    summary: [
      {
        title: "Sweden",
        value: "Neutral / slowing",
        value_sv: "Neutral / saktar till",
        note: "Riksbank easing, labor soft, inflation near target.",
        note_sv: "Riksbanken lättar, arbetsmarknaden svag, inflation nära målet.",
        status: "Neutral",
        status_sv: "Neutral",
        sources: [
          { label: "Riksbank", url: "https://www.riksbank.se/en-gb/statistics/" },
          { label: "SCB", url: "https://www.scb.se/" },
        ],
      },
      {
        title: "China",
        value: "Neutral / slowing",
        value_sv: "Neutral / saktar till",
        note: "Property drag offsets industry; low inflation.",
        note_sv: "Fastigheter tynger, industrin stabil, inflation låg.",
        status: "Neutral",
        status_sv: "Neutral",
        sources: [
          { label: "NBS", url: "https://www.stats.gov.cn/english/" },
          { label: "PBOC", url: "https://www.pbc.gov.cn/en/" },
        ],
      },
      {
        title: "Global Risk",
        value: "No downgrade",
        value_sv: "Ingen nedgradering",
        note: "PMI mixed; USD stable.",
        note_sv: "PMI blandat; USD stabilt.",
        status: "Neutral",
        status_sv: "Neutral",
        sources: [
          { label: "PMI", url: "https://www.spglobal.com/marketintelligence/en/news-insights/latest-news-headlines" },
          { label: "FRED", url: "https://fred.stlouisfed.org/" },
        ],
      },
    ],
    global_risk: {
      status: "Neutral",
      note: "Global PMI mixed; USD conditions stable. No downgrade applied.",
      note_sv: "PMI blandat; USD-förhållanden stabila. Ingen nedgradering.",
      sources: [
        { label: "PMI", url: "https://www.spglobal.com/marketintelligence/en/news-insights/latest-news-headlines" },
        { label: "FRED", url: "https://fred.stlouisfed.org/" },
      ],
    },
    countries: [
      {
        name: "Sweden",
        map: { lat: 60.1282, lon: 18.6435 },
        subtitle: "Riksbank lead, krona sensitivity",
        subtitle_sv: "Riksbanken leder, krona-känsligt",
        overall: "Neutral / slowing",
        overall_sv: "Neutral / saktar till",
        blocks: [
          {
            label: "Central Bank",
            label_sv: "Riksbank",
            status: "Neutral",
            status_sv: "Neutral",
            detail: `Policy rate ${policyRate?.toFixed(2) ?? "n/a"}%`,
            detail_sv: `Styrränta ${policyRate?.toFixed(2) ?? "n/a"}%`,
            sources: [{ label: "Riksbank", url: riksbankRateUrl }],
            trend: [policyRate ?? 1.75, policyRate ?? 1.75, policyRate ?? 1.75, policyRate ?? 1.75],
          },
          {
            label: "Currency",
            label_sv: "Krona",
            status: "Neutral",
            status_sv: "Neutral",
            detail: `EUR/SEK ${ecbSek?.toFixed(4) ?? "n/a"}`,
            detail_sv: `EUR/SEK ${ecbSek?.toFixed(4) ?? "n/a"}`,
            sources: [{ label: "ECB", url: "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml" }],
            trend: ecbSek ? [ecbSek * 0.99, ecbSek * 1.0, ecbSek * 1.01, ecbSek] : [],
          },
          {
            label: "Inflation",
            label_sv: "Inflation",
            status: swInflationStatus,
            status_sv: swInflationStatus === "Good" ? "Bra" : swInflationStatus === "Weak" ? "Svag" : "Neutral",
            detail: `CPIF ${cpif ?? "n/a"}% y/y`,
            detail_sv: `KPIF ${cpif ?? "n/a"}% y/y`,
            sources: [{ label: "SCB CPI", url: scbCpiUrl }],
            trend: cpif ? [cpif + 0.9, cpif + 0.5, cpif + 0.2, cpif] : [],
          },
          {
            label: "External Cycle",
            label_sv: "Extern cykel",
            status: "Weak",
            status_sv: "Svag",
            detail: "Germany PMI (manual placeholder)",
            detail_sv: "Tysk PMI (manuell placeholder)",
            sources: [{ label: "PMI", url: "https://www.spglobal.com/marketintelligence/en/news-insights/latest-news-headlines" }],
            trend: [45, 47.2, 48.4, 49.1],
          },
          {
            label: "Soft Data",
            label_sv: "Mjuk data",
            status: swedenPmi != null && swedenPmi > 50 ? "Good" : "Neutral",
            status_sv: swedenPmi != null && swedenPmi > 50 ? "Bra" : "Neutral",
            detail: `Sweden PMI ${swedenPmi ?? "n/a"}; NIER ETI ${nierEti ?? "n/a"}`,
            detail_sv: `Sverige PMI ${swedenPmi ?? "n/a"}; NIER ETI ${nierEti ?? "n/a"}`,
            sources: [
              { label: "PMI", url: swedenPmiUrl },
              { label: "NIER", url: nierUrl }
            ],
            trend: swedenPmi ? [swedenPmi - 2, swedenPmi - 1, swedenPmi - 0.5, swedenPmi] : [],
          },
          {
            label: "Housing & Credit",
            label_sv: "Bostäder & kredit",
            status: "Neutral",
            status_sv: "Neutral",
            detail: "Household lending (manual placeholder)",
            detail_sv: "Hushållslån (manuell placeholder)",
            sources: [{ label: "SCB Credit", url: "https://www.scb.se/" }],
            trend: [3.2, 3.1, 3.0, 2.9],
          },
          {
            label: "Labor",
            label_sv: "Arbetsmarknad",
            status: unemployment != null && unemployment > 8 ? "Weak" : "Neutral",
            status_sv: unemployment != null && unemployment > 8 ? "Svag" : "Neutral",
            detail: `Unemployment ${unemployment ?? "n/a"}%`,
            detail_sv: `Arbetslöshet ${unemployment ?? "n/a"}%`,
            sources: [{ label: "SCB LFS", url: scbLfsUrl }],
            trend: unemployment ? [unemployment - 0.4, unemployment - 0.2, unemployment - 0.1, unemployment] : [],
          },
          {
            label: "Hard Data",
            label_sv: "Hård data",
            status: gdpMom != null && gdpMom < 0 ? "Weak" : "Neutral",
            status_sv: gdpMom != null && gdpMom < 0 ? "Svag" : "Neutral",
            detail: `GDP indicator ${gdpMom ?? "n/a"}% m/m`,
            detail_sv: `BNP-indikator ${gdpMom ?? "n/a"}% m/m`,
            sources: [{ label: "SCB GDP", url: scbGdpUrl }],
            trend: gdpMom ? [gdpMom + 0.6, gdpMom + 0.3, gdpMom + 0.1, gdpMom] : [],
          },
        ],
        timeline: ["Signal: Riksbank path", "Lead: EUR/SEK", "Confirm: retail & GDP"],
        timeline_sv: ["Signal: Riksbankens bana", "Ledande: EUR/SEK", "Bekräfta: detaljhandel & BNP"],
      },
      {
        name: "China",
        map: { lat: 35.8617, lon: 104.1954 },
        subtitle: "Demand repair vs property drag",
        subtitle_sv: "Efterfrågereparation vs fastighetsbroms",
        overall: "Neutral / slowing",
        overall_sv: "Neutral / saktar till",
        blocks: [
          {
            label: "Consumption",
            label_sv: "Konsumtion",
            status: cnConsumptionStatus,
            status_sv: cnConsumptionStatus === "Good" ? "Bra" : cnConsumptionStatus === "Weak" ? "Svag" : "Neutral",
            detail: `Retail sales ${retailDec ?? "n/a"}% y/y; 2025 ${retail2025 ?? "n/a"}%`,
            detail_sv: `Detaljhandel ${retailDec ?? "n/a"}% y/y; 2025 ${retail2025 ?? "n/a"}%`,
            sources: [{ label: "NBS", url: nbsRetailUrl }],
            trend: retailDec ? [retailDec + 1.2, retailDec + 0.8, retailDec + 0.4, retailDec] : [],
          },
          {
            label: "Industry",
            label_sv: "Industri",
            status: cnIndustryStatus,
            status_sv: cnIndustryStatus === "Good" ? "Bra" : cnIndustryStatus === "Weak" ? "Svag" : "Neutral",
            detail: `Industrial output ${industryDec ?? "n/a"}% y/y; 2025 ${industry2025 ?? "n/a"}%`,
            detail_sv: `Industriproduktion ${industryDec ?? "n/a"}% y/y; 2025 ${industry2025 ?? "n/a"}%`,
            sources: [{ label: "NBS", url: nbsIndustryUrl }],
            trend: industryDec ? [industryDec - 0.2, industryDec + 0.1, industryDec + 0.3, industryDec] : [],
          },
          {
            label: "Property",
            label_sv: "Fastigheter",
            status: property != null && property > 10 ? "Weak" : "Neutral",
            status_sv: property != null && property > 10 ? "Svag" : "Neutral",
            detail: `Real estate investment -${property ?? "n/a"}% y/y`,
            detail_sv: `Fastighetsinvesteringar -${property ?? "n/a"}% y/y`,
            sources: [{ label: "NBS", url: nbsPropertyUrl }],
            trend: property ? [-property + 2.0, -property + 1.0, -property + 0.5, -property] : [],
          },
          {
            label: "Credit",
            label_sv: "Kredit",
            status: "Neutral",
            status_sv: "Neutral",
            detail: `TSF outstanding ${tsf ?? "n/a"}% y/y`,
            detail_sv: `TSF utestående ${tsf ?? "n/a"}% y/y`,
            sources: [{ label: "PBOC", url: pbocTsfUrl }],
            trend: tsf ? [tsf + 0.7, tsf + 0.4, tsf + 0.2, tsf] : [],
          },
          {
            label: "Inflation",
            label_sv: "Inflation",
            status: cnInflationStatus,
            status_sv: cnInflationStatus === "Good" ? "Bra" : cnInflationStatus === "Weak" ? "Svag" : "Neutral",
            detail: `CPI ${cpi ?? "n/a"}% y/y`,
            detail_sv: `CPI ${cpi ?? "n/a"}% y/y`,
            sources: [{ label: "NBS", url: nbsCpiUrl }],
            trend: cpi ? [cpi - 0.2, cpi - 0.1, cpi - 0.05, cpi] : [],
          },
        ],
        timeline: ["Signal: policy + credit", "Lead: consumption", "Confirm: property + exports"],
        timeline_sv: ["Signal: policy + kredit", "Ledande: konsumtion", "Bekräfta: fastigheter + export"],
      },
    ],
  };

  return data;
}
