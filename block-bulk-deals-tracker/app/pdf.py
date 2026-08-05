"""The tracker as a document to read, rather than a sheet to work in.

The same figures as the Excel file, laid out for someone looking through the
deals rather than pivoting them: market data first, then the deals with their
counterparties, then the news. Each section starts on its own page and is
bookmarked, so the reader can jump straight to the part they came for.
"""

import io
import re
from datetime import date

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = HexColor("#16191d")
MUTED = HexColor("#6b7280")
FAINT = HexColor("#9aa1ab")
RULE = HexColor("#dfe3e8")
HEADER = HexColor("#1f2937")
BAND = HexColor("#f7f8f9")
# Live move / volume spike — whole market row.
MOVE_ROW = HexColor("#dbeafe")
UP = HexColor("#0b7a4b")
DOWN = HexColor("#b42318")
LINK = HexColor("#1d4ed8")
LIVE_MOVE_THRESHOLD = 3.0

DASH = "\u2014"
PAGE = landscape(A4)
MARGIN = 11 * mm
WIDTH = PAGE[0] - 2 * MARGIN

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7, leading=8.8)
HEAD = ParagraphStyle(
    "head", fontName="Helvetica-Bold", fontSize=6.6, leading=8, textColor=white
)
HEAD_CENTRE = ParagraphStyle("headCentre", parent=HEAD, alignment=1)
PARTY = ParagraphStyle("party", fontName="Helvetica", fontSize=6.8, leading=8.6, textColor=MUTED)
PARTY_TAG = ParagraphStyle("partyTag", parent=PARTY, fontName="Helvetica-Bold", textColor=INK)
TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=INK)
SECTION = ParagraphStyle(
    "section", fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=INK
)
NOTE = ParagraphStyle("note", fontName="Helvetica", fontSize=7, leading=9.2, textColor=MUTED)
STAT_LABEL = ParagraphStyle(
    "statLabel", fontName="Helvetica", fontSize=6.8, leading=8.4, textColor=FAINT
)
STAT_VALUE = ParagraphStyle(
    "statValue", fontName="Helvetica-Bold", fontSize=11, leading=13.5, textColor=INK
)
HEADLINE = ParagraphStyle("headline", parent=BODY, textColor=LINK)


def _indian(value, digits=0) -> str:
    """Digit grouping as it is written in India, so 14444800 reads 1,44,44,800."""
    if value is None:
        return DASH
    whole, _, fraction = f"{abs(value):.{digits}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        whole = ",".join(pairs + [tail])
    text = f"{whole}.{fraction}" if fraction else whole
    return f"-{text}" if value < 0 else text


def _signed(value) -> str:
    """Returns and discounts read as movements, so they keep their sign."""
    if value is None:
        return DASH
    # A deal struck within a rounding error of the close is flat, not "-0.00".
    return "0.00" if round(value, 2) == 0 else f"{value:+,.2f}"


def _plain(value, digits=2) -> str:
    return DASH if value is None else f"{value:,.{digits}f}"


def _day(value) -> str:
    if not value:
        return DASH
    try:
        return date.fromisoformat(str(value)).strftime("%d %b %Y")
    except ValueError:
        return str(value)


# The built-in PDF fonts cover Western European text and nothing else, and a
# character outside that lands on the page as a black box. Symbols that have a
# plain equivalent are swapped for it; the rupee sign becomes "Rs", which is what
# the rest of the tracker writes anyway.
_SWAPS = {
    "\u20b9": "Rs ",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2026": "...",
    "\u00a0": " ",
    "\u200b": "",
    "\ufffd": "",
}
_LOOSE_PUNCTUATION = re.compile(r"\s+([,.;:!?])")
_REPEATED_PUNCTUATION = re.compile(r"([,;:])(?:\s*\1)+")


def _clean(text) -> str:
    """Text reduced to what the page can actually draw.

    Anything left over is dropped rather than shown as a box or a question mark.
    A Hindi headline keeps the company names inside it and loses the rest, which
    is worth more to a reader than a row of boxes, and the link still works.
    """
    text = str(text or "")
    for symbol, plain in _SWAPS.items():
        text = text.replace(symbol, plain)
    text = text.encode("cp1252", "ignore").decode("cp1252")
    text = _LOOSE_PUNCTUATION.sub(r"\1", text)
    text = _REPEATED_PUNCTUATION.sub(r"\1", text)
    return " ".join(text.split())


def _loses_words(text) -> bool:
    """Whether cleaning would drop letters, rather than just tidy punctuation."""
    text = str(text or "")
    for symbol, plain in _SWAPS.items():
        text = text.replace(symbol, plain)
    return any(
        character.isalpha() and not character.encode("cp1252", "ignore")
        for character in text
    )


def _escape(text) -> str:
    return _clean(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tint(value):
    """Green for a rise or a discount, red for the other way."""
    if value is None:
        return None
    if value > 0:
        return UP
    if value < 0:
        return DOWN
    return None


def _big_live_move(entry) -> bool:
    """True when a highlight trigger fired (or legacy ±3% on live returns)."""
    if "highlight_row" in entry:
        return bool(entry.get("highlight_row"))
    for field in ("intraday_return_pct", "daily_return_pct"):
        value = entry.get(field)
        if value is None:
            continue
        try:
            if abs(float(value)) > LIVE_MOVE_THRESHOLD:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _trigger_cell(entry, flag: str, field: str | None = None) -> bool:
    """Bold the cell that fired a highlight; fall back to ±3% when flags absent."""
    if flag in entry:
        return bool(entry.get(flag))
    if field is None:
        return False
    value = entry.get(field)
    if value is None:
        return False
    try:
        return abs(float(value)) > LIVE_MOVE_THRESHOLD
    except (TypeError, ValueError):
        return False


def _stamp(canvas, number, total, footer):
    base = 9 * mm
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, base + 4 * mm, PAGE[0] - MARGIN, base + 4 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(FAINT)
    canvas.drawString(MARGIN, base, footer)
    canvas.drawRightString(PAGE[0] - MARGIN, base, f"Page {number} of {total}")


class _Document(SimpleDocTemplate):
    """Registers a bookmark for each section heading it lays down."""

    def afterFlowable(self, flowable):
        mark = getattr(flowable, "bookmark", None)
        if mark:
            key, title = mark
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level=0)


def _heading(number, title):
    para = Paragraph(f"{number}&nbsp;&nbsp; {_escape(title)}", SECTION)
    para.bookmark = (f"section{number}", title)
    return para


def _grid(extra=(), size=7.5, pad=4):
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 1), HEADER),
            ("VALIGN", (0, 0), (-1, 1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, 1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, 1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), pad),
            ("RIGHTPADDING", (0, 0), (-1, -1), pad),
            ("TOPPADDING", (0, 2), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 2), (-1, -1), 3.5),
            ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
            ("TEXTCOLOR", (0, 2), (-1, -1), INK),
            ("FONT", (0, 2), (-1, -1), "Helvetica", size),
            *extra,
        ]
    )


def _no_rows(message):
    table = Table([[Paragraph(message, NOTE)]], colWidths=[WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BAND),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


# ------------------------------------------------------------------ the cover

def _scope(meta) -> str:
    chosen = meta.get("companies") or []
    if not chosen:
        return "All companies"
    if len(chosen) <= 3:
        return ", ".join(company["name"] for company in chosen)
    return f"{len(chosen)} companies"


def _period(meta) -> str:
    if meta.get("since_listing"):
        return f"Since listing to {_day(meta.get('to_date'))}"
    return f"{_day(meta.get('from_date'))} to {_day(meta.get('to_date'))}"


def _cover(meta) -> list:
    stats = [
        ("Period", _period(meta)),
        ("Companies", _scope(meta)),
        ("Minimum deal size", f"Rs {_indian(meta.get('min_deal_size_cr') or 0)} cr"),
        ("Deals", _indian(meta.get("deal_count") or 0)),
        ("Total value", f"Rs {_indian(meta.get('total_value_cr') or 0)} cr"),
    ]
    widths = [70 * mm, 70 * mm, 47 * mm, 34 * mm, 54 * mm]
    table = Table(
        [
            [Paragraph(label.upper(), STAT_LABEL) for label, _ in stats],
            [Paragraph(_escape(value), STAT_VALUE) for _, value in stats],
        ],
        colWidths=widths,
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
                ("LEFTPADDING", (1, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, RULE),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
            ]
        )
    )
    return [Paragraph("Block &amp; Bulk Deals Tracker", TITLE), Spacer(1, 7), table]


# ------------------------------------------------------------- market section

_PERIODS = ("1d", "1w", "1m")
_PERIOD_LABEL = {"1d": "1D", "1w": "1W", "1m": "1M"}
# Content-sized base widths (mm). Company names drop "Limited", so more width
# goes to the live / return / volume figures. Fewer windows → larger uniform scale.
_COMPANY = 34
_LIVE = [14, 12, 12, 16, 16]  # CMP, intraday, daily, live volume, mkt cap
_RETURNS = [12, 12, 12]
_VOLUME_COL = {"adtv": 16, "vwap": 14, "delivery": 13}
_PAGE_MM = WIDTH / mm


def _volume_periods(windows) -> list:
    wanted = windows or {"1d": True, "1w": True, "1m": True}
    return [period for period in _PERIODS if wanted.get(period, True)]


def _market_natural_widths(periods) -> list:
    """Column widths sized to what each metric needs — not stretched to the page."""
    n = len(periods)
    volume = []
    if n:
        volume = (
            [_VOLUME_COL["adtv"]] * n
            + [_VOLUME_COL["vwap"]] * n
            + [_VOLUME_COL["delivery"]] * n
        )
    return [_COMPANY] + list(_LIVE) + list(_RETURNS) + volume


def _market_scale(periods) -> float:
    """Magnify the whole table so content-sized columns fill the page."""
    return _PAGE_MM / sum(_market_natural_widths(periods))


def _market_widths(periods) -> list:
    scale = _market_scale(periods)
    return [width * scale for width in _market_natural_widths(periods)]


def _scaled_style(base: ParagraphStyle, scale: float, name: str) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        parent=base,
        fontSize=base.fontSize * scale,
        leading=base.leading * scale,
    )


# Default full market layout (all volume windows). Tests check this sums to page width.
MARKET_WIDTHS = _market_widths(["1d", "1w", "1m"])


def _market_table(entries, windows=None) -> Table:
    periods = _volume_periods(windows)
    widths = _market_widths(periods)
    scale = _market_scale(periods)
    head = _scaled_style(HEAD, scale, "marketHead")
    head_centre = _scaled_style(HEAD_CENTRE, scale, "marketHeadCentre")
    company = _scaled_style(SMALL, scale, "marketCompany")
    company_bold = ParagraphStyle(
        "marketCompanyBold",
        parent=company,
        fontName="Helvetica-Bold",
    )
    body_size = 6.5 * scale
    pad = 2.5 * scale
    fixed = 6  # company, CMP, intraday, daily, live volume, mkt cap
    return_start = fixed
    volume_start = return_start + 3
    single = len(periods) == 1

    top_labels = [
        "Company",
        "CMP NSE (Rs)",
        "Intraday (%)",
        "Daily (%)",
        "Vol NSE",
        "MCap (Rs cr)",
        "Return (%)",
        "",
        "",
    ]
    second_labels = [""] * fixed + ["1D", "1W", "1M"]
    spans = [("SPAN", (column, 0), (column, 1)) for column in range(fixed)]
    spans.append(("SPAN", (return_start, 0), (return_start + 2, 0)))

    cursor = volume_start
    if single:
        # One window: one readable header per metric, not a lonely "1W" under a wide title.
        tag = _PERIOD_LABEL[periods[0]]
        for title in (
            f"ADTV {tag} (Rs cr)",
            f"VWAP {tag} (Rs)",
            f"Delivery {tag} (%)",
        ):
            top_labels.append(title)
            second_labels.append("")
            spans.append(("SPAN", (cursor, 0), (cursor, 1)))
            cursor += 1
    else:
        for label in ("ADTV (Rs cr)", "VWAP (Rs)", "Delivery (%)"):
            if not periods:
                break
            top_labels.extend([label] + [""] * (len(periods) - 1))
            second_labels.extend(_PERIOD_LABEL[period] for period in periods)
            end = cursor + len(periods) - 1
            if end > cursor:
                spans.append(("SPAN", (cursor, 0), (end, 0)))
            cursor = end + 1

    top = [
        Paragraph(_escape(text), head_centre if index >= 1 else head)
        for index, text in enumerate(top_labels)
    ]
    second = [
        Paragraph(_escape(text), head_centre) if text else ""
        for text in second_labels
    ]

    data = [top, second]
    tints = []
    highlights = []
    bold_cells = []
    for index, entry in enumerate(entries, start=2):
        big = _big_live_move(entry)
        row = [
            Paragraph(
                _escape(entry.get("company_name")),
                company_bold if big else company,
            ),
            _plain(entry.get("cmp")),
            _signed(entry.get("intraday_return_pct")),
            _signed(entry.get("daily_return_pct")),
            _indian(entry.get("live_volume")),
            _indian(entry.get("market_cap_cr")),
            _signed(entry.get("return_1d_pct")),
            _signed(entry.get("return_1w_pct")),
            _signed(entry.get("return_1m_pct")),
        ]
        for period in periods:
            row.append(_plain(entry.get(f"adtv_{period}_cr")))
        for period in periods:
            row.append(_plain(entry.get(f"vwap_{period}")))
        for period in periods:
            row.append(_plain(entry.get(f"delivery_{period}_pct")))
        data.append(row)
        if big:
            highlights.append(("BACKGROUND", (0, index), (-1, index), MOVE_ROW))
        for column, flag, field in (
            (2, "highlight_intraday", "intraday_return_pct"),
            (3, "highlight_daily", "daily_return_pct"),
            (4, "highlight_volume", None),
        ):
            if _trigger_cell(entry, flag, field):
                bold_cells.append(
                    ("FONTNAME", (column, index), (column, index), "Helvetica-Bold")
                )
        for column, field in (
            (2, "intraday_return_pct"),
            (3, "daily_return_pct"),
            (6, "return_1d_pct"),
            (7, "return_1w_pct"),
            (8, "return_1m_pct"),
        ):
            colour = _tint(entry.get(field))
            if colour is not None:
                tints.append(("TEXTCOLOR", (column, index), (column, index), colour))

    rules = [
        ("LINEAFTER", (0, 0), (0, -1), 0.4, RULE),
        ("LINEAFTER", (4, 0), (4, -1), 0.4, RULE),
        ("LINEAFTER", (5, 0), (5, -1), 0.4, RULE),
        ("LINEAFTER", (8, 0), (8, -1), 0.4, RULE),
    ]
    cursor = volume_start
    step = 1 if single else max(len(periods), 1)
    for _ in range(3 if periods else 0):
        rules.append(
            ("LINEAFTER", (cursor + step - 1, 0), (cursor + step - 1, -1), 0.4, RULE)
        )
        cursor += step

    table = Table(
        data,
        colWidths=[width * mm for width in widths],
        repeatRows=2,
        hAlign="LEFT",
    )
    table.setStyle(
        _grid(
            [
                *spans,
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 2), (-1, -1), "MIDDLE"),
                *rules,
                *highlights,
                *bold_cells,
                *tints,
            ],
            size=body_size,
            pad=pad,
        )
    )
    return table


# -------------------------------------------------------------- deals section

DEAL_WIDTHS = [55, 18, 23, 24, 32, 19, 18, 15, 15, 18, 22, 16]


def _deal_head() -> tuple:
    top = [
        "Company",
        "Trade date",
        "Ticker",
        "Quantity",
        "Price (Rs)",
        "Deal size (Rs cr)",
        "Prev close (Rs)",
        "Discount (%)",
        "Month before the deal",
        "",
        "",
        "",
    ]
    cells = [
        Paragraph(_escape(text), HEAD_CENTRE if index == 8 else HEAD)
        for index, text in enumerate(top)
    ]
    second = [""] * 8 + [
        Paragraph(text, HEAD_CENTRE)
        for text in ("Return (%)", "ADTV (Rs cr)", "VWAP (Rs)", "Delivery (%)")
    ]
    spans = [("SPAN", (column, 0), (column, 1)) for column in range(8)]
    spans.append(("SPAN", (8, 0), (11, 0)))
    return [cells, second], spans


def _parties(row) -> Paragraph:
    """Type, exchange and both sides of the deal, given the full page width."""
    label = " &middot; ".join(
        part for part in (_escape(row.get("deal_type")), _escape(row.get("exchange"))) if part
    )
    lines = [f'<font color="#6b7280">{label}</font>'] if label else []
    for side in ("sellers", "buyers"):
        text = _escape(row.get(side))
        if text:
            lines.append(f'<font color="#16191d"><b>{side.title()}</b></font>&nbsp; {text}')
    return Paragraph("<br/>".join(lines) or DASH, PARTY)


def _deals_table(rows) -> Table:
    (head, second), spans = _deal_head()
    data = [head, second]
    style = list(spans)

    for row in rows:
        index = len(data)
        data.append(
            [
                Paragraph(_escape(row.get("company_name")), BODY),
                row.get("trade_date") or DASH,
                row.get("ticker") or DASH,
                _indian(row.get("quantity")),
                row.get("price") or DASH,
                _plain(row.get("deal_size_cr")),
                _plain(row.get("prev_close")),
                _signed(row.get("discount_pct")),
                _signed(row.get("pre_return_1m_pct")),
                _plain(row.get("pre_adtv_1m_cr")),
                _plain(row.get("pre_vwap_1m")),
                _plain(row.get("pre_delivery_1m_pct")),
            ]
        )
        data.append([_parties(row)] + [""] * 11)

        for column, field in ((7, "discount_pct"), (8, "pre_return_1m_pct")):
            colour = _tint(row.get(field))
            if colour is not None:
                style.append(("TEXTCOLOR", (column, index), (column, index), colour))

        # The counterparties belong to the figures above them, so the pair reads
        # as one entry, the rule falls between deals rather than inside one, and
        # a page break never leaves a list of buyers stranded from its deal.
        style += [
            ("SPAN", (0, index + 1), (-1, index + 1)),
            ("NOSPLIT", (0, index), (-1, index + 1)),
            ("LINEBELOW", (0, index), (-1, index), 0, white),
            ("TOPPADDING", (0, index + 1), (-1, index + 1), 0),
            ("BOTTOMPADDING", (0, index + 1), (-1, index + 1), 6),
            ("LINEBELOW", (0, index + 1), (-1, index + 1), 0.4, RULE),
        ]

    table = Table(
        data,
        colWidths=[width * mm for width in DEAL_WIDTHS],
        repeatRows=2,
        hAlign="LEFT",
        splitInRow=1,
    )
    table.setStyle(
        _grid(
            [
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 2), (-1, -1), "TOP"),
                ("LINEAFTER", (7, 0), (7, 1), 0.4, RULE),
                *style,
            ]
        )
    )
    return table


# --------------------------------------------------------------- news section

NEWS_WIDTHS = [50, 28, 28, 169]


def _moment(value) -> str:
    text = str(value or "").replace("T", " ")
    return text[:16] if text else DASH


def _news_table(articles) -> Table:
    head = [Paragraph(text, HEAD) for text in ("Company", "Source", "Published", "Headline")]
    # A second header row keeps every table in the document built the same way.
    data = [head, [""] * 4]
    style = [("SPAN", (column, 0), (column, 1)) for column in range(4)]

    previous = None
    for article in articles:
        index = len(data)
        company = article.get("company_name")
        fresh = company != previous
        previous = company

        url = _escape(article.get("url"))
        headline = _escape(article.get("headline"))
        text = f'<link href="{url}">{headline}</link>' if url else headline
        data.append(
            [
                Paragraph(_escape(company), BODY) if fresh else "",
                Paragraph(_escape(article.get("source")), BODY),
                _moment(article.get("published")),
                Paragraph(text, HEADLINE),
            ]
        )
        if fresh and index > 2:
            style.append(("LINEABOVE", (0, index), (-1, index), 0.4, RULE))

    table = Table(
        data,
        colWidths=[width * mm for width in NEWS_WIDTHS],
        repeatRows=2,
        hAlign="LEFT",
    )
    table.setStyle(
        _grid(
            [
                ("VALIGN", (0, 2), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 1), (-1, -1), 0, white),
                *style,
            ]
        )
    )
    return table


# ---------------------------------------------------------------- the whole thing

POINT = ParagraphStyle("point", parent=BODY, leftIndent=8, bulletIndent=0, leading=10)


def _quarter_blocks(entries) -> list:
    """One company per block: quarter label, report link, then short takeaways."""
    if not entries:
        return [_no_rows("No quarterly filings found for these companies.")]

    blocks = []
    for entry in entries:
        url = _escape(entry.get("report_url"))
        label = _escape(entry.get("report_label") or "Report")
        link = f'<link href="{url}">{label}</link>' if url else DASH
        basis = " · ".join(
            part
            for part in (
                _escape(entry.get("consolidated")),
                _escape(entry.get("audited")),
                "QoQ" if entry.get("compare") == "QoQ" else "",
            )
            if part
        )
        head = Table(
            [
                [
                    Paragraph(f"<b>{_escape(entry.get('company_name'))}</b>", BODY),
                    Paragraph(_escape(entry.get("quarter") or DASH), BODY),
                    Paragraph(link, HEADLINE),
                ],
                [
                    Paragraph(
                        f"{_escape(entry.get('ticker') or '')}"
                        + (f"  ·  {basis}" if basis else ""),
                        NOTE,
                    ),
                    "",
                    "",
                ],
            ],
            colWidths=[95 * mm, 70 * mm, 110 * mm],
        )
        head.setStyle(
            TableStyle(
                [
                    ("SPAN", (0, 1), (2, 1)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.4, RULE),
                    ("TOPPADDING", (0, 0), (-1, 0), 6),
                ]
            )
        )
        blocks.append(head)
        for point in entry.get("takeaways") or []:
            blocks.append(Paragraph(f"• {_escape(point)}", POINT))
        blocks.append(Spacer(1, 6))
    return blocks


def _story(rows, market_data, articles, meta, quarters=None) -> list:
    quarters = quarters or []
    articles = articles or []
    wanted = (meta or {}).get("sections") or {
        "deals": True,
        "market": True,
        "news": True,
        "quarters": True,
    }
    # Title + period/deals stats only belong with the deals run.
    include_deals = wanted.get("deals", True)
    story = _cover(meta) if include_deals else []
    index = 0

    def add_section(title, body):
        nonlocal index, story
        index += 1
        if index == 1 and not include_deals:
            story += [_heading(index, title), Spacer(1, 4)]
        else:
            gap = 13 if index == 1 else 16
            story += [Spacer(1, gap), _heading(index, title), Spacer(1, 4)]
        story.extend(body)

    if wanted.get("market", True):
        add_section(
            "Market data",
            [
                _market_table(market_data, meta.get("market_windows"))
                if market_data
                else _no_rows("No market data for this search.")
            ],
        )

    if wanted.get("quarters", True):
        add_section("Latest quarter", _quarter_blocks(quarters))

    if include_deals:
        add_section(
            "Block & bulk deals",
            [
                _deals_table(rows)
                if rows
                else _no_rows("No deals matched this search. Try a lower minimum deal size.")
            ],
        )

    if wanted.get("news", True):
        days = meta.get("news_window_days") or 3
        add_section(
            "News",
            [
                _news_table(articles)
                if articles
                else _no_rows(
                    f"Nothing published about these companies in the last {days} days."
                )
            ],
        )

    return story


def _render(story, footer, total) -> tuple:
    stream = io.BytesIO()
    document = _Document(
        stream,
        pagesize=PAGE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=12 * mm,
        bottomMargin=17 * mm,
        title="Block & Bulk Deals Tracker",
        author="Block & Bulk Deals Tracker",
    )
    stamp = lambda canvas, doc: _stamp(canvas, doc.page, total, footer)  # noqa: E731
    document.build(story, onFirstPage=stamp, onLaterPages=stamp)
    return stream.getvalue(), document.page


def _footer(meta) -> str:
    """Left-side page label: deals branding only when that section is in the run."""
    wanted = (meta or {}).get("sections") or {
        "deals": True,
        "market": True,
        "news": True,
        "quarters": True,
    }
    if wanted.get("deals", True):
        return f"Block & Bulk Deals Tracker  |  {_period(meta)}  |  {_scope(meta)}"
    labels = []
    if wanted.get("market", True):
        labels.append("Market data")
    if wanted.get("quarters", True):
        labels.append("Latest quarter")
    if wanted.get("news", True):
        labels.append("News")
    left = "  ·  ".join(labels) or "Report"
    return f"{left}  |  {_scope(meta)}"


def build_pdf(rows, market_data=None, articles=None, meta=None, quarters=None) -> bytes:
    meta = meta or {}
    rows = rows or []
    market_data = market_data or []
    articles = articles or []
    quarters = quarters or []
    footer = _footer(meta)

    # A footer that says which page of how many can only be written once the
    # length is known, so the first run is thrown away for its page count. The
    # alternative, holding the pages back and stamping them on the way out,
    # leaves every bookmark pointing at page one. Laying out a document this
    # size costs a few milliseconds either way.
    story = lambda: _story(rows, market_data, articles, meta, quarters)  # noqa: E731
    _, total = _render(story(), footer, 0)
    raw, _ = _render(story(), footer, total)
    return raw
