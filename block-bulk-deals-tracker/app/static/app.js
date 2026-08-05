const form = document.getElementById("controls");
const companyInput = document.getElementById("company");
const clearCompany = document.getElementById("clear-company");
const suggestions = document.getElementById("suggestions");
const companyChosen = document.getElementById("company-chosen");
const fromInput = document.getElementById("from-date");
const toInput = document.getElementById("to-date");
const minInput = document.getElementById("min-size");
const modeRange = document.getElementById("mode-range");
const modeListing = document.getElementById("mode-listing");
const rangeFrom = document.getElementById("range-fields");
const rangeTo = document.getElementById("range-fields-to");
const listingNote = document.getElementById("listing-note");
const generateButton = document.getElementById("generate");
const downloadButton = document.getElementById("download");
const downloadPdfButton = document.getElementById("download-pdf");
const includeDeals = document.getElementById("include-deals");
const includeMarket = document.getElementById("include-market");
const includeQuarters = document.getElementById("include-quarters");
const includeNews = document.getElementById("include-news");
const marketVol1d = document.getElementById("market-vol-1d");
const marketVol1w = document.getElementById("market-vol-1w");
const marketVol1m = document.getElementById("market-vol-1m");
const marketWindowChecks = document.getElementById("market-window-checks");
const statusLine = document.getElementById("status");
const picker = document.getElementById("picker");
const pickerMessage = document.getElementById("picker-message");
const pickerList = document.getElementById("picker-list");
const summary = document.getElementById("summary");
const marketSection = document.getElementById("market");
const marketNote = document.getElementById("market-note");
const marketHead = document.querySelector("#market-table thead");
const marketBody = document.querySelector("#market-table tbody");
const quartersSection = document.getElementById("quarters");
const quartersNote = document.getElementById("quarters-note");
const quartersBody = document.querySelector("#quarters-table tbody");
const newsSection = document.getElementById("news");
const newsNote = document.getElementById("news-note");
const newsBody = document.querySelector("#news-table tbody");
const results = document.getElementById("results");
const dealsNote = document.getElementById("deals-note");
const tableBody = document.querySelector("#deals tbody");

// Names the user has pinned to a specific listing, keyed by the text they typed.
// Anything not in here is sent as free text for the server to resolve.
const resolvedByTerm = new Map();
let sinceListing = false;
let lastQuery = null;

const isoDate = (date) => date.toISOString().slice(0, 10);
const today = new Date();
toInput.value = isoDate(today);
fromInput.value = isoDate(new Date(today.getTime() - 30 * 86400000));

const prettyDate = (iso) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });

function setStatus(message, isError = false) {
  statusLine.textContent = message;
  statusLine.classList.toggle("error", isError);
}

/* ---------------------------------------------------------------- company */

function describeCompany(company) {
  const parts = [company.name];
  if (company.ticker) parts.push(company.ticker);
  parts.push(company.exchanges.join(" + ") || "unlisted");
  return parts.join(" \u00b7 ");
}

function splitTerms(text) {
  const seen = new Set();
  const terms = [];
  for (const part of (text || "").split(";")) {
    const term = part.trim();
    if (term && !seen.has(term.toLowerCase())) {
      seen.add(term.toLowerCase());
      terms.push(term);
    }
  }
  return terms;
}

/** The name the caret is sitting in, so autocomplete follows the one being typed. */
function activeTerm() {
  const value = companyInput.value;
  const caret = companyInput.selectionStart ?? value.length;
  const start = value.lastIndexOf(";", caret - 1) + 1;
  const next = value.indexOf(";", caret);
  const end = next === -1 ? value.length : next;
  return { start, end, text: value.slice(start, end).trim() };
}

function replaceActiveTerm(name) {
  const { start, end } = activeTerm();
  const value = companyInput.value;
  const lead = start > 0 ? " " : "";
  companyInput.value = value.slice(0, start) + lead + name + value.slice(end);
  const caret = start + lead.length + name.length;
  companyInput.setSelectionRange(caret, caret);
}

/** Companies pinned to a listing, in the order they appear in the box. */
function chosenCompanies() {
  return splitTerms(companyInput.value)
    .map((term) => resolvedByTerm.get(term.toLowerCase()))
    .filter(Boolean);
}

function renderChosen() {
  const chosen = chosenCompanies();
  const pending = splitTerms(companyInput.value).length - chosen.length;
  companyChosen.innerHTML = "";
  clearCompany.hidden = companyInput.value.length === 0;

  if (!chosen.length) {
    companyChosen.hidden = true;
    updateListingNote();
    return;
  }
  for (const company of chosen) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = describeCompany(company);
    companyChosen.appendChild(chip);
  }
  if (pending > 0) {
    const note = document.createElement("span");
    note.className = "chip pending";
    note.textContent = `${pending} more to look up`;
    companyChosen.appendChild(note);
  }
  companyChosen.hidden = false;
  updateListingNote();
}

function selectCompany(company) {
  replaceActiveTerm(company.name);
  resolvedByTerm.set(company.name.toLowerCase(), company);
  renderChosen();
  hideSuggestions();
  picker.hidden = true;
  companyInput.focus();
}

/** Swap one ambiguous name for the listing the user picked, leaving the rest alone. */
function settleTerm(term, company) {
  const terms = splitTerms(companyInput.value).map((item) =>
    item.toLowerCase() === term.toLowerCase() ? company.name : item
  );
  companyInput.value = terms.join("; ");
  resolvedByTerm.set(company.name.toLowerCase(), company);
  renderChosen();
  picker.hidden = true;
}

function adoptCompanies(list) {
  for (const company of list) resolvedByTerm.set(company.name.toLowerCase(), company);
  if (list.length) companyInput.value = list.map((company) => company.name).join("; ");
  renderChosen();
}

function clearSelection() {
  resolvedByTerm.clear();
  companyInput.value = "";
  companyChosen.hidden = true;
  clearCompany.hidden = true;
  hideSuggestions();
  if (sinceListing) setMode(false);
  updateListingNote();
}

let searchTimer = null;

function hideSuggestions() {
  clearTimeout(searchTimer);
  suggestions.hidden = true;
  suggestions.innerHTML = "";
}

companyInput.addEventListener("input", () => {
  // A name that has been edited is no longer the one that was looked up.
  const live = new Set(splitTerms(companyInput.value).map((term) => term.toLowerCase()));
  for (const term of [...resolvedByTerm.keys()]) {
    if (!live.has(term)) resolvedByTerm.delete(term);
  }
  renderChosen();

  const term = activeTerm().text;
  clearTimeout(searchTimer);
  if (term.length < 2 || resolvedByTerm.has(term.toLowerCase())) {
    hideSuggestions();
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      const response = await fetch(`/api/companies?q=${encodeURIComponent(term)}`);
      const data = await response.json();
      renderSuggestions(data.companies);
    } catch (error) {
      hideSuggestions();
    }
  }, 180);
});

function renderSuggestions(list) {
  suggestions.innerHTML = "";
  if (!list.length) {
    hideSuggestions();
    return;
  }
  for (const company of list) {
    const item = document.createElement("li");
    item.innerHTML = `<strong></strong><span></span>`;
    item.querySelector("strong").textContent = company.name;
    item.querySelector("span").textContent = [
      company.ticker,
      company.exchanges.join(" + "),
      company.listing_date ? `listed ${prettyDate(company.listing_date)}` : "",
    ]
      .filter(Boolean)
      .join(" \u00b7 ");
    item.addEventListener("mousedown", (event) => {
      event.preventDefault();
      selectCompany(company);
    });
    suggestions.appendChild(item);
  }
  suggestions.hidden = false;
}

companyInput.addEventListener("blur", () => setTimeout(hideSuggestions, 120));
clearCompany.addEventListener("click", clearSelection);

/* ----------------------------------------------------------------- period */

function setMode(listing) {
  sinceListing = listing;
  modeListing.classList.toggle("active", listing);
  modeRange.classList.toggle("active", !listing);
  modeListing.setAttribute("aria-pressed", String(listing));
  modeRange.setAttribute("aria-pressed", String(!listing));
  rangeFrom.hidden = listing;
  rangeTo.hidden = listing;
  updateListingNote();
}

function updateListingNote() {
  if (!sinceListing) {
    listingNote.hidden = true;
    return;
  }
  const chosen = chosenCompanies();
  if (!chosen.length) {
    listingNote.textContent = "Name a company to search since its listing date.";
  } else {
    // Each company starts on its own listing date, so each one is spelled out.
    const spans = chosen.map((company) =>
      company.listing_date
        ? `${company.name} from ${prettyDate(company.listing_date)}`
        : `${company.name} from the start of the archive`
    );
    listingNote.textContent = `Searching ${spans.join(", ")} \u2014 each until today.`;
  }
  listingNote.hidden = false;
}

modeRange.addEventListener("click", () => setMode(false));
modeListing.addEventListener("click", () => {
  if (!companyInput.value.trim()) {
    setStatus("Name at least one company first to search since listing.", true);
    companyInput.focus();
    return;
  }
  setMode(true);
});

/* -------------------------------------------------------------- requests */

function selectedSections() {
  if (!includeDeals || !includeMarket || !includeNews || !includeQuarters) {
    throw new Error(
      "Section checkboxes did not load. Hard-refresh the page (Ctrl+F5) and try again."
    );
  }
  if (!marketVol1d || !marketVol1w || !marketVol1m) {
    throw new Error(
      "Market window checkboxes did not load. Hard-refresh the page (Ctrl+F5) and try again."
    );
  }
  return {
    include_deals: Boolean(includeDeals.checked),
    include_market: Boolean(includeMarket.checked),
    include_news: Boolean(includeNews.checked),
    include_quarters: Boolean(includeQuarters.checked),
    market_volume_1d: Boolean(marketVol1d.checked),
    market_volume_1w: Boolean(marketVol1w.checked),
    market_volume_1m: Boolean(marketVol1m.checked),
  };
}

function syncMarketWindowControls() {
  const on = includeMarket.checked;
  marketWindowChecks.classList.toggle("is-disabled", !on);
  marketVol1d.disabled = !on;
  marketVol1w.disabled = !on;
  marketVol1m.disabled = !on;
}

includeMarket.addEventListener("change", syncMarketWindowControls);
syncMarketWindowControls();

function volumePeriods(windows) {
  const wanted = windows || { "1d": true, "1w": true, "1m": true };
  return ["1d", "1w", "1m"].filter((period) => wanted[period]);
}

function fillMarketHead(periods) {
  marketHead.innerHTML = "";
  const top = document.createElement("tr");
  const sub = document.createElement("tr");
  const fixed = [
    ["Company", false],
    ["CMP NSE (Rs)", true],
    ["Intraday (%)", true],
    ["Daily (%)", true],
    ["Volume NSE", true],
    ["MCap (Rs cr)", true],
  ];
  for (const [label, numeric] of fixed) {
    const th = document.createElement("th");
    th.rowSpan = 2;
    th.textContent = label;
    if (numeric) th.className = "numeric";
    top.appendChild(th);
  }
  const families = [["Return (%)", ["1d", "1w", "1m"]]];
  if (periods.length) {
    families.push(
      ["ADTV (Rs cr)", periods],
      ["VWAP (Rs)", periods],
      ["Delivery (%)", periods]
    );
  }
  const labels = { "1d": "1D", "1w": "1W", "1m": "1M" };
  for (const [label, keys] of families) {
    const th = document.createElement("th");
    th.className = "numeric group";
    th.colSpan = keys.length;
    th.textContent = label;
    top.appendChild(th);
    keys.forEach((key, index) => {
      const cell = document.createElement("th");
      cell.className = index === 0 ? "numeric sub group" : "numeric sub";
      cell.textContent = labels[key];
      sub.appendChild(cell);
    });
  }
  marketHead.appendChild(top);
  marketHead.appendChild(sub);
}

function hideAllSections() {
  summary.hidden = true;
  marketSection.hidden = true;
  quartersSection.hidden = true;
  newsSection.hidden = true;
  results.hidden = true;
}

function readQuery() {
  const query = {
    min_deal_size_cr: Number(minInput.value),
    since_listing: sinceListing,
    ...selectedSections(),
  };
  const keys = [];
  const unresolved = [];
  for (const term of splitTerms(companyInput.value)) {
    const company = resolvedByTerm.get(term.toLowerCase());
    if (company) keys.push(company.key);
    else unresolved.push(term);
  }
  if (keys.length) query.company_keys = keys;
  if (unresolved.length) query.company = unresolved.join("; ");
  if (!sinceListing) {
    query.from_date = fromInput.value;
    query.to_date = toInput.value;
  }
  return query;
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      /* not JSON */
    }
    const detail = payload && payload.detail;
    if (detail && detail.candidates) {
      const ambiguous = new Error(detail.message);
      ambiguous.candidates = detail.candidates;
      ambiguous.term = detail.term;
      throw ambiguous;
    }
    if (typeof detail === "string") throw new Error(detail);
    if (Array.isArray(detail) && detail.length && detail[0].msg) {
      throw new Error(detail[0].msg.replace(/^Value error, /, ""));
    }
    throw new Error(`Request failed (${response.status})`);
  }
  return response;
}

function showPicker(message, candidates, term) {
  pickerMessage.textContent = message;
  pickerList.innerHTML = "";
  for (const company of candidates) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.innerHTML = `<strong></strong><span></span>`;
    button.querySelector("strong").textContent = company.name;
    button.querySelector("span").textContent = [
      company.ticker,
      company.exchanges.join(" + "),
      company.listing_date ? `listed ${prettyDate(company.listing_date)}` : "",
    ]
      .filter(Boolean)
      .join(" \u00b7 ");
    button.addEventListener("click", () => {
      settleTerm(term, company);
      form.requestSubmit();
    });
    item.appendChild(button);
    pickerList.appendChild(item);
  }
  picker.hidden = false;
}

/* -------------------------------------------------------------- rendering */

function renderSummary(data, chosen) {
  // Since-listing gives each company its own start, so the span is labelled as
  // the widest one rather than pretending every company was searched from it.
  const periodLabel = data.since_listing && chosen.length > 1 ? "Widest period" : "Period";
  const entries = [
    ["Deals", data.deal_count.toLocaleString("en-IN")],
    [
      "Total value",
      `Rs ${data.total_value_cr.toLocaleString("en-IN", { maximumFractionDigits: 0 })} cr`,
    ],
    [periodLabel, `${prettyDate(data.from_date)} \u2013 ${prettyDate(data.to_date)}`],
    ["Threshold", `Rs ${data.min_deal_size_cr.toLocaleString("en-IN")} cr`],
    ["Self-trades excluded", data.self_trade_entities_excluded.toLocaleString("en-IN")],
  ];
  if (chosen.length === 1) {
    entries.unshift(["Company", `${chosen[0].name} (${chosen[0].ticker})`]);
  } else if (chosen.length > 1) {
    entries.unshift([
      `Companies (${chosen.length})`,
      chosen.map((company) => company.ticker || company.name).join(", "),
    ]);
  }

  summary.innerHTML = "";
  for (const [label, value] of entries) {
    const box = document.createElement("div");
    box.innerHTML = `<span></span><strong></strong>`;
    box.querySelector("span").textContent = label;
    box.querySelector("strong").textContent = value;
    summary.appendChild(box);
  }
  summary.hidden = false;
}

const DASH = "\u2014";

const decimals = (value, digits = 2) =>
  value === null || value === undefined
    ? DASH
    : value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });

function renderMarketData(entries, notes, windows) {
  marketBody.innerHTML = "";
  marketHead.innerHTML = "";
  if (!entries || !entries.length) {
    marketSection.hidden = true;
    return;
  }

  const periods = volumePeriods(windows);
  fillMarketHead(periods);

  const asOf = entries.map((entry) => entry.as_of).filter(Boolean).sort().pop();
  const lines = [
    asOf
      ? `Trailing figures from NSE daily history as of ${prettyDate(
          asOf
        )}, adjusted for splits and bonuses.`
      : "Trailing figures from NSE daily history.",
    "CMP NSE / Intraday % / Daily % / Volume NSE are NSE session figures (blank if that feed is unavailable). Return % always shows 1D, 1W and 1M. ADTV / VWAP / Delivery follow the window checkboxes above.",
    "Market cap is BSE full market capitalisation. These snapshots do not change with the period above.",
  ];
  if (notes && notes.length) lines.push(notes.join(" "));
  marketNote.textContent = lines.join(" ");

  const whole = (value) =>
    value === null || value === undefined ? DASH : value.toLocaleString("en-IN");

  const fragment = document.createDocumentFragment();
  for (const entry of entries) {
    const tr = document.createElement("tr");
    if (entry.highlight_row) tr.classList.add("highlight-row");
    const cells = [
      [entry.company_name, "company"],
      [decimals(entry.cmp), "numeric"],
      [decimals(entry.intraday_return_pct), "numeric signed"],
      [decimals(entry.daily_return_pct), "numeric signed"],
      [whole(entry.live_volume), "numeric"],
      [decimals(entry.market_cap_cr, 0), "numeric"],
      [decimals(entry.return_1d_pct), "numeric signed group"],
      [decimals(entry.return_1w_pct), "numeric signed"],
      [decimals(entry.return_1m_pct), "numeric signed"],
    ];
    periods.forEach((period, index) => {
      cells.push([
        decimals(entry[`adtv_${period}_cr`]),
        index === 0 ? "numeric group" : "numeric",
      ]);
    });
    periods.forEach((period, index) => {
      cells.push([decimals(entry[`vwap_${period}`]), index === 0 ? "numeric group" : "numeric"]);
    });
    periods.forEach((period, index) => {
      cells.push([
        decimals(entry[`delivery_${period}_pct`]),
        index === 0 ? "numeric group" : "numeric",
      ]);
    });
    const signed = {
      2: entry.intraday_return_pct,
      3: entry.daily_return_pct,
      6: entry.return_1d_pct,
      7: entry.return_1w_pct,
      8: entry.return_1m_pct,
    };
    const triggers = {
      2: entry.highlight_intraday,
      3: entry.highlight_daily,
      4: entry.highlight_volume,
    };

    cells.forEach(([value, className], index) => {
      const td = document.createElement("td");
      td.className = className;
      td.textContent = value;
      if (className === "company") td.title = entry.company_name;
      if (className.includes("signed")) {
        const change = signed[index];
        if (change > 0) td.classList.add("up");
        if (change < 0) td.classList.add("down");
      }
      if (triggers[index]) td.classList.add("trigger");
      tr.appendChild(td);
    });
    fragment.appendChild(tr);
  }
  marketBody.appendChild(fragment);
  marketSection.hidden = false;
}

const SOURCE_LABELS = [
  "CNBC TV18",
  "Moneycontrol",
  "NDTV Profit",
  "Economic Times",
  "Business Standard",
  "Mint",
];

function prettyMoment(iso) {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return iso || "";
  return moment.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function renderQuarters(entries, notes) {
  quartersBody.innerHTML = "";
  if (!entries || !entries.length) {
    quartersSection.hidden = true;
    return;
  }

  const lines = [
    "Latest quarter from NSE Integrated Filings. Takeaways show the absolute figure and the move versus a year earlier (YoY), or the previous quarter when that filing is missing (QoQ).",
  ];
  if (notes && notes.length) lines.push(notes.join(" "));
  quartersNote.textContent = lines.join(" ");

  const fragment = document.createDocumentFragment();
  for (const entry of entries) {
    const tr = document.createElement("tr");

    const company = document.createElement("td");
    company.className = "company";
    const title = document.createElement("div");
    title.textContent = entry.company_name;
    company.appendChild(title);
    const meta = document.createElement("div");
    meta.className = "quarter-meta";
    const bits = [entry.ticker, entry.consolidated, entry.audited].filter(Boolean);
    if (entry.compare === "QoQ") bits.push("QoQ");
    meta.textContent = bits.join(" · ");
    company.appendChild(meta);
    company.dataset.copy = entry.company_name;
    tr.appendChild(company);

    const quarter = document.createElement("td");
    quarter.className = "nowrap";
    quarter.textContent = entry.quarter || DASH;
    tr.appendChild(quarter);

    const report = document.createElement("td");
    report.className = "nowrap";
    if (entry.report_url) {
      const link = document.createElement("a");
      link.href = entry.report_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = entry.report_label || "Report";
      report.appendChild(link);
    } else {
      report.textContent = DASH;
    }
    tr.appendChild(report);

    const takeaways = document.createElement("td");
    takeaways.className = "takeaways";
    const list = document.createElement("ul");
    for (const point of entry.takeaways || []) {
      const item = document.createElement("li");
      item.textContent = point;
      list.appendChild(item);
    }
    takeaways.appendChild(list);
    takeaways.dataset.copy = (entry.takeaways || []).map((point) => `• ${point}`).join("\n");
    tr.appendChild(takeaways);

    fragment.appendChild(tr);
  }
  quartersBody.appendChild(fragment);
  quartersSection.hidden = false;
}

function renderNews(articles, windowDays) {
  newsBody.innerHTML = "";
  const days = windowDays || 3;
  if (!articles || !articles.length) {
    newsNote.textContent = `Nothing published in the last ${days} days by ${SOURCE_LABELS.join(
      ", "
    )}.`;
    newsSection.hidden = false;
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "empty";
    cell.textContent = "No stories found for these companies in the window.";
    row.appendChild(cell);
    newsBody.appendChild(row);
    return;
  }

  const companies = new Set(articles.map((article) => article.company_name)).size;
  newsNote.textContent = `${articles.length} ${
    articles.length === 1 ? "story" : "stories"
  } across ${companies} ${
    companies === 1 ? "company" : "companies"
  } from the last ${days} days, from ${SOURCE_LABELS.join(
    ", "
  )}. Only headlines that name the company are kept, most relevant first.`;

  const fragment = document.createDocumentFragment();
  let previous = null;
  for (const article of articles) {
    const tr = document.createElement("tr");
    const isNewCompany = article.company_name !== previous;
    if (isNewCompany && previous !== null) tr.className = "group-start";
    previous = article.company_name;

    const company = document.createElement("td");
    company.className = "company";
    // Repeats are left blank on screen to keep the grouping readable, but every
    // copied row needs to stand on its own once it is pasted elsewhere.
    company.textContent = isNewCompany ? article.company_name : "";
    company.dataset.copy = article.company_name;
    tr.appendChild(company);

    const source = document.createElement("td");
    source.className = "nowrap source";
    source.textContent = article.source;
    tr.appendChild(source);

    const published = document.createElement("td");
    published.className = "nowrap";
    published.textContent = prettyMoment(article.published);
    tr.appendChild(published);

    const headline = document.createElement("td");
    headline.className = "headline";
    const link = document.createElement("a");
    link.href = article.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = article.headline;
    headline.appendChild(link);
    tr.appendChild(headline);

    fragment.appendChild(tr);
  }
  newsBody.appendChild(fragment);
  newsSection.hidden = false;
}

const DEAL_COLUMNS = 17;

function renderRows(rows) {
  tableBody.innerHTML = "";
  dealsNote.textContent =
    "Prev Close is the last NSE close before the trade date. Discount compares it with the deal's own weighted price, Deal Size divided by Quantity, so a block struck below the close reads positive. The month before the deal is the 30 calendar days ending at that same close.";

  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = DEAL_COLUMNS;
    cell.className = "empty";
    cell.textContent = "No deals matched this search. Try a lower minimum deal size.";
    row.appendChild(cell);
    tableBody.appendChild(row);
    results.hidden = false;
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement("tr");
    const cells = [
      [row.company_name, "company"],
      [row.trade_date, "nowrap"],
      [row.year, "numeric"],
      [row.ticker, "nowrap"],
      [row.quantity.toLocaleString("en-IN"), "numeric"],
      [row.price, "numeric"],
      [row.deal_size_cr.toLocaleString("en-IN", { minimumFractionDigits: 2 }), "numeric"],
      [decimals(row.prev_close), "numeric group"],
      [decimals(row.discount_pct), "numeric signed"],
      [decimals(row.pre_return_1m_pct), "numeric signed group"],
      [decimals(row.pre_adtv_1m_cr), "numeric"],
      [decimals(row.pre_vwap_1m), "numeric"],
      [decimals(row.pre_delivery_1m_pct), "numeric"],
      [row.deal_type, "nowrap"],
      [row.sellers, "parties"],
      [row.buyers, "parties"],
      [row.exchange, "nowrap"],
    ];
    // A discount is good news for the buyer and a fall in the run-up is not, so
    // only the return is coloured by direction; the discount is coloured by
    // whether the block priced below the close at all.
    const signs = { 8: row.discount_pct, 9: row.pre_return_1m_pct };

    cells.forEach(([value, className], index) => {
      const td = document.createElement("td");
      td.className = className;
      if (className === "parties") {
        // A single deal can have dozens of counterparties; keep the row height sane.
        const box = document.createElement("div");
        box.className = "clamp";
        box.textContent = value || "\u2014";
        box.title = value;
        td.appendChild(box);
      } else {
        td.textContent = value;
      }
      if (className.includes("signed")) {
        const change = signs[index];
        if (change > 0) td.classList.add("up");
        if (change < 0) td.classList.add("down");
      }
      if (index === 7 && row.prev_close_day) {
        td.title = `Close on ${prettyDate(row.prev_close_day)}`;
      }
      tr.appendChild(td);
    });
    fragment.appendChild(tr);
  }
  tableBody.appendChild(fragment);
  results.hidden = false;
}

/* ------------------------------------------------------------------- copy */

/** One label per column, joining a grouped header to the row beneath it. */
function headerLabels(table) {
  const rows = [...table.tHead.rows];
  const grid = [];
  rows.forEach((tr, rowIndex) => {
    let column = 0;
    for (const cell of tr.cells) {
      while (grid[rowIndex] && grid[rowIndex][column] !== undefined) column += 1;
      const text = cell.textContent.trim();
      for (let down = 0; down < (cell.rowSpan || 1); down += 1) {
        for (let across = 0; across < (cell.colSpan || 1); across += 1) {
          grid[rowIndex + down] = grid[rowIndex + down] || [];
          grid[rowIndex + down][column + across] = text;
        }
      }
      column += cell.colSpan || 1;
    }
  });

  return grid[0].map((_, column) => {
    const parts = [];
    for (const line of grid) {
      const text = line[column];
      if (text && parts[parts.length - 1] !== text) parts.push(text);
    }
    return parts.join(" ");
  });
}

function cellValue(td) {
  const text = (td.dataset.copy ?? td.textContent).trim();
  if (text === DASH) return "";
  // Indian digit grouping does not survive a paste into a spreadsheet, and a
  // number that arrives as text is no use to whoever pasted it.
  return td.classList.contains("numeric") ? text.replace(/,/g, "") : text;
}

/** The table as labels and rows, with a URL column beside any linked column. */
function tableMatrix(table) {
  const body = table.tBodies[0];
  const rows = [...body.rows]
    .filter((tr) => !tr.querySelector("td.empty"))
    .map((tr) => [...tr.cells].map((td) => ({
      text: cellValue(td),
      href: td.querySelector("a")?.href || "",
    })));

  let labels = headerLabels(table);
  const linked = labels.map((_, index) => rows.some((row) => row[index] && row[index].href));
  if (linked.some(Boolean)) {
    labels = labels.flatMap((label, index) => (linked[index] ? [label, `${label} link`] : [label]));
    for (let index = 0; index < rows.length; index += 1) {
      rows[index] = rows[index].flatMap((cell, column) =>
        linked[column] ? [cell, { text: cell.href, href: "" }] : [cell]
      );
    }
  }
  return { labels, rows };
}

const flat = (text) => text.replace(/\s*\n\s*/g, " ").replace(/\t/g, " ");

const toTsv = ({ labels, rows }) =>
  [labels, ...rows.map((row) => row.map((cell) => cell.text))]
    .map((line) => line.map(flat).join("\t"))
    .join("\n");

const escapeHtml = (text) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function toHtml({ labels, rows }) {
  const head = labels.map((label) => `<th>${escapeHtml(label)}</th>`).join("");
  const body = rows
    .map((row) => {
      const cells = row
        .map(({ text, href }) => {
          const safe = escapeHtml(text);
          return `<td>${href ? `<a href="${escapeHtml(href)}">${safe}</a>` : safe}</td>`;
        })
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function legacyCopy(text) {
  const box = document.createElement("textarea");
  box.value = text;
  box.style.position = "fixed";
  box.style.opacity = "0";
  document.body.appendChild(box);
  box.focus();
  box.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (error) {
    copied = false;
  }
  box.remove();
  return copied;
}

function flash(button, message) {
  if (button.dataset.label === undefined) button.dataset.label = button.textContent;
  button.textContent = message;
  button.classList.add("done");
  clearTimeout(Number(button.dataset.timer));
  button.dataset.timer = String(
    setTimeout(() => {
      button.textContent = button.dataset.label;
      button.classList.remove("done");
    }, 1800)
  );
}

async function copyTable(table, button) {
  const data = tableMatrix(table);
  if (!data.rows.length) {
    flash(button, "Nothing to copy");
    return;
  }

  const tsv = toTsv(data);
  let copied = true;
  try {
    // Both flavours: a spreadsheet takes the HTML and keeps the links, anything
    // plainer takes the tab-separated text.
    if (navigator.clipboard && window.ClipboardItem) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/plain": new Blob([tsv], { type: "text/plain" }),
          "text/html": new Blob([toHtml(data)], { type: "text/html" }),
        }),
      ]);
    } else if (navigator.clipboard) {
      await navigator.clipboard.writeText(tsv);
    } else {
      copied = legacyCopy(tsv);
    }
  } catch (error) {
    // A refused permission or a window that never took focus leaves the older
    // selection-based route, which browsers still honour from a click.
    copied = legacyCopy(tsv);
  }

  const count = `${data.rows.length} row${data.rows.length === 1 ? "" : "s"}`;
  flash(button, copied ? `Copied ${count}` : "Could not reach the clipboard");
}

for (const button of document.querySelectorAll("[data-copy-table]")) {
  button.addEventListener("click", () => {
    const table = document.querySelector(button.dataset.copyTable);
    if (table) copyTable(table, button);
  });
}

/* ----------------------------------------------------------------- events */

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideSuggestions();
  picker.hidden = true;

  const query = readQuery();
  if (
    !query.include_deals &&
    !query.include_market &&
    !query.include_news &&
    !query.include_quarters
  ) {
    setStatus("Select at least one section to generate.", true);
    return;
  }
  if (!query.include_deals && !query.company_keys && !query.company) {
    setStatus("Name at least one company when Block & bulk deals is unchecked.", true);
    companyInput.focus();
    return;
  }
  if (!sinceListing && query.to_date < query.from_date) {
    setStatus("'To' date must not be earlier than 'From' date.", true);
    return;
  }

  generateButton.disabled = true;
  downloadButton.disabled = true;
  downloadPdfButton.disabled = true;
  hideAllSections();
  const parts = [
    query.include_deals && "deals",
    query.include_market && "market data",
    query.include_quarters && "takeaways",
    query.include_news && "news",
  ].filter(Boolean);
  setStatus(`Fetching ${parts.join(", ")}. Unchecked sections are skipped.`);

  try {
    const response = await postJson("/api/tracker", query);
    const data = await response.json();
    const chosen = data.companies || [];
    // Trust the server echo of what it actually ran — never invent "all on".
    const sections = data.sections;
    if (!sections) {
      throw new Error(
        "Server did not return section flags. Hard-refresh the page (Ctrl+F5) and try again."
      );
    }
    adoptCompanies(chosen);
    renderSummary(data.summary, chosen);
    if (sections.market) {
      renderMarketData(data.market_data, data.market_notes, data.market_windows);
    } else marketSection.hidden = true;
    if (sections.quarters) renderQuarters(data.quarters, data.quarter_notes);
    else quartersSection.hidden = true;
    if (sections.news) renderNews(data.news, data.news_window_days);
    else newsSection.hidden = true;
    if (sections.deals) renderRows(data.rows);
    else results.hidden = true;
    // Everything is pinned by now, so the download never has to resolve names again.
    lastQuery = { ...query, ...selectedSections() };
    delete lastQuery.company;
    if (chosen.length) lastQuery.company_keys = chosen.map((company) => company.key);
    downloadButton.disabled = false;
    downloadPdfButton.disabled = false;
    const warning = data.warnings.length ? ` Partial data - ${data.warnings.join("; ")}` : "";
    const ran = [
      sections.deals && `${data.rows.length} deals`,
      sections.market && `${(data.market_data || []).length} market rows`,
      sections.quarters && "takeaways",
      sections.news && "news",
    ].filter(Boolean);
    setStatus(`Done: ${ran.join(", ")}.${warning}`, Boolean(warning));
  } catch (error) {
    hideAllSections();
    if (error.candidates) {
      setStatus("");
      showPicker(error.message, error.candidates, error.term);
    } else {
      setStatus(error.message, true);
    }
  } finally {
    generateButton.disabled = false;
  }
});

async function download(path, extension, label) {
  if (!lastQuery) return;
  downloadButton.disabled = true;
  downloadPdfButton.disabled = true;
  setStatus(`Preparing ${label} file.`);
  try {
    const response = await postJson(path, lastQuery);
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = match ? match[1] : `block-bulk-deals.${extension}`;
    link.click();
    URL.revokeObjectURL(link.href);
    setStatus(`${label} downloaded.`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    downloadButton.disabled = false;
    downloadPdfButton.disabled = false;
  }
}

downloadButton.addEventListener("click", () => download("/api/tracker.xlsx", "xlsx", "Excel"));
downloadPdfButton.addEventListener("click", () => download("/api/tracker.pdf", "pdf", "PDF"));
