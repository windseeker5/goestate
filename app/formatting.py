"""Money and number rendering — the single source of truth for how figures look.

Registered as Jinja filters in app/__init__.py, so templates never hand-roll a
format string. Deliberately dependency-free: Python's own format spec already
does grouped thousands (f"{n:,.2f}"), and the value here is in the policy, not
in the arithmetic.

The policy:
  - Tables, detail pages and totals show exact figures with cents, always. An
    estate ledger is an accounting document — every number an executor reports
    to heirs has to be exact and copy-pasteable. Fixed 2 decimals also keep
    decimal points aligned down a right-aligned column.
  - KPI tiles drop the cents and abbreviate only above $1M, where a full figure
    would overflow the card. The exact value goes in a title= tooltip alongside.
  - Anything that isn't a number renders as an em dash rather than raising.

Locale is en-CA ($492,501.00). Switching to fr-CA (492 501,00 $) means changing
this file only — that's the reason it exists.
"""

CURRENCY_SYMBOL = "$"

BLANK = "—"  # em dash

# Descending, so the first match wins.
_COMPACT_UNITS = (
    (1_000_000_000_000, "T"),
    (1_000_000_000, "B"),
    (1_000_000, "M"),
)


def _to_number(value):
    """Coerce whatever came out of SQLite into a float, or None if it can't be.

    The money columns are REAL, but the create/edit routes write
    `request.form.get(...) or 0` with no cast, so a non-numeric string can land
    in the column as TEXT. Returning None here means such a row renders as a
    dash instead of blowing up the whole page at render time.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def money(value, blank=BLANK, zero_blank=False):
    """Exact money for tables, detail pages and totals: $350,000.00, -$4,000.00.

    zero_blank=True renders 0 as the blank placeholder. Use it on per-row value
    columns: `estimated_value` is REAL DEFAULT 0 and the routes coerce empty
    input to 0, so a stored zero means "never entered" far more often than it
    means "worth nothing". Totals and KPIs leave it off, where $0.00 is a real
    and meaningful answer.
    """
    number_value = _to_number(value)
    if number_value is None:
        return blank
    if zero_blank and number_value == 0:
        return blank

    sign = "-" if number_value < 0 else ""
    # Sign outside the symbol: -$4,000.00, not $-4,000.00.
    return f"{sign}{CURRENCY_SYMBOL}{abs(number_value):,.2f}"


def money_compact(value, blank=BLANK, threshold=1_000_000):
    """Headline money for KPI tiles: $492,501 under the threshold, $1.25M above.

    Cents are dropped — they're noise on a six-figure headline. Always pair this
    with money() in a title= attribute so the exact figure stays one hover away;
    the compact form is a summary, never the number of record.
    """
    number_value = _to_number(value)
    if number_value is None:
        return blank

    sign = "-" if number_value < 0 else ""
    magnitude = abs(number_value)

    if magnitude < threshold:
        return f"{sign}{CURRENCY_SYMBOL}{magnitude:,.0f}"

    for unit, suffix in _COMPACT_UNITS:
        if magnitude >= unit:
            scaled = magnitude / unit
            # Three significant digits: 1.25M, 12.5M, 125M.
            decimals = 2 if scaled < 10 else (1 if scaled < 100 else 0)
            text = f"{scaled:.{decimals}f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")  # 1.20M -> 1.2M
            return f"{sign}{CURRENCY_SYMBOL}{text}{suffix}"

    # threshold below 1M: fall back to the exact whole-dollar form.
    return f"{sign}{CURRENCY_SYMBOL}{magnitude:,.0f}"


def number(value, blank=BLANK):
    """Plain counts with thousands grouping: 1,234."""
    number_value = _to_number(value)
    if number_value is None:
        return blank
    return f"{number_value:,.0f}"
