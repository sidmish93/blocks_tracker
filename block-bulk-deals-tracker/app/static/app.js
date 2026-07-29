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
const statusLine = document.getElementById("status");
const picker = document.getElementById("picker");
const pickerMessage = document.getElementById("picker-message");
const pickerList = document.getElementById("picker-list");
const summary = document.getElementById("summary");
const marketSection = document.getElementById("market");
const marketNote = document.getElementById("market-note");
const marketBody = document.querySelector("#market-table tbody");
const newsSection = document.getElementById("news");
const newsNote = document.getElementById("news-note");
const newsBody = document.querySelector("#news-table tbody");
const results = document.getElementById("results");
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

function readQuery() {
  const query = { min_deal_size_cr: Number(minInput.value), since_listing: sinceListing };
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

function renderMarketData(entries, notes) {
  marketBody.innerHTML = "";
  if (!entries || !entries.length) {
    marketSection.hidden = true;
    return;
  }

  const asOf = entries.map((entry) => entry.as_of).filter(Boolean).sort().pop();
  const lines = [
    asOf
      ? `Trailing figures from NSE daily history as of ${prettyDate(
          asOf
        )}, adjusted for splits and bonuses.`
      : "Trailing figures from NSE daily history.",
    "Market cap is BSE full market capitalisation. These are current snapshots and do not change with the period above.",
  ];
  if (notes && notes.length) lines.push(notes.join(" "));
  marketNote.textContent = lines.join(" ");

  const fragment = document.createDocumentFragment();
  for (const entry of entries) {
    const tr = document.createElement("tr");
    const cells = [
      [entry.company_name, "company"],
      [entry.ticker || DASH, "nowrap"],
      [entry.as_of ? prettyDate(entry.as_of) : DASH, "nowrap"],
      [decimals(entry.close), "numeric"],
      [decimals(entry.market_cap_cr, 0), "numeric"],
      [decimals(entry.return_1d_pct), "numeric signed group"],
      [decimals(entry.return_1w_pct), "numeric signed"],
      [decimals(entry.return_1m_pct), "numeric signed"],
      [decimals(entry.adtv_1d_cr), "numeric group"],
      [decimals(entry.adtv_1w_cr), "numeric"],
      [decimals(entry.adtv_1m_cr), "numeric"],
      [decimals(entry.vwap_1d), "numeric group"],
      [decimals(entry.vwap_1w), "numeric"],
      [decimals(entry.vwap_1m), "numeric"],
      [decimals(entry.delivery_1d_pct), "numeric group"],
      [decimals(entry.delivery_1w_pct), "numeric"],
      [decimals(entry.delivery_1m_pct), "numeric"],
    ];
    const returns = [entry.return_1d_pct, entry.return_1w_pct, entry.return_1m_pct];

    cells.forEach(([value, className], index) => {
      const td = document.createElement("td");
      td.className = className;
      td.textContent = value;
      if (className === "company") td.title = entry.company_name;
      if (className.includes("signed")) {
        const change = returns[index - 5];
        if (change > 0) td.classList.add("up");
        if (change < 0) td.classList.add("down");
      }
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
    company.textContent = isNewCompany ? article.company_name : "";
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

function renderRows(rows) {
  tableBody.innerHTML = "";
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 11;
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
      [row.deal_type, "nowrap"],
      [row.sellers, "parties"],
      [row.buyers, "parties"],
      [row.exchange, "nowrap"],
    ];
    for (const [value, className] of cells) {
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
      tr.appendChild(td);
    }
    fragment.appendChild(tr);
  }
  tableBody.appendChild(fragment);
  results.hidden = false;
}

/* ----------------------------------------------------------------- events */

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideSuggestions();
  picker.hidden = true;

  const query = readQuery();
  if (!sinceListing && query.to_date < query.from_date) {
    setStatus("'To' date must not be earlier than 'From' date.", true);
    return;
  }

  generateButton.disabled = true;
  downloadButton.disabled = true;
  setStatus(
    sinceListing
      ? "Fetching every disclosure since listing. This can take a minute the first time."
      : "Fetching NSE and BSE disclosures. Long ranges can take a minute."
  );

  try {
    const response = await postJson("/api/tracker", query);
    const data = await response.json();
    const chosen = data.companies || [];
    adoptCompanies(chosen);
    renderSummary(data.summary, chosen);
    renderMarketData(data.market_data, data.market_notes);
    renderNews(data.news, data.news_window_days);
    renderRows(data.rows);
    // Everything is pinned by now, so the download never has to resolve names again.
    lastQuery = { ...query };
    delete lastQuery.company;
    if (chosen.length) lastQuery.company_keys = chosen.map((company) => company.key);
    downloadButton.disabled = false;
    const warning = data.warnings.length ? ` Partial data - ${data.warnings.join("; ")}` : "";
    setStatus(`${data.rows.length} deals found.${warning}`, Boolean(warning));
  } catch (error) {
    summary.hidden = true;
    marketSection.hidden = true;
    newsSection.hidden = true;
    results.hidden = true;
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

downloadButton.addEventListener("click", async () => {
  if (!lastQuery) return;
  downloadButton.disabled = true;
  setStatus("Preparing Excel file.");
  try {
    const response = await postJson("/api/tracker.xlsx", lastQuery);
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = match ? match[1] : "block-bulk-deals.xlsx";
    link.click();
    URL.revokeObjectURL(link.href);
    setStatus("Excel downloaded.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    downloadButton.disabled = false;
  }
});
