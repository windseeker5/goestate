"""Estate money: the bank account, the cash each helper holds, and bank checks.

Two kinds of accounts (table `money_accounts`):
  - one 'bank' account  — the estate (succession) bank account
  - one 'held' account per person — sale cash they collected and still hold

Every movement of money is one row in `money_entries`, amount in signed
INTEGER cents (+ in, - out):
  sale     +held   a helper sold an item and kept the cash
  deposit  -held / +bank, two rows sharing a transfer_id
  bill     -bank or -held   a bill paid from the bank or from cash on hand
  income / interest / fee / adjustment / other   manual lines

Every entry that is real estate money in or out also carries a `category`
(see CATEGORIES) — what the final account to the heirs groups by. Deposits
and corrections only move money around, so they have none.

"What Ken owes the estate" is simply the balance of Ken's held account.

The bank balance starts from an opening balance read off a bank printout:
bank entries dated on or before `opening_date` are already inside that number,
so they are shown but not added again.

A bank check ("reconciliation") stamps `reconciliation_id` on the bank entries
the user ticked off against a statement; stamped entries are locked.
"""

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

BACKFILL_FLAG = "money_backfill_v1"

# Asset status for money the bank put straight into the estate account
# (TFSA/CELI, RRIF/FERR, insurance payout). The asset stays on the inventory
# but no longer counts in the net estate: its money is in the bank balance.
IN_BANK = "In bank account"
# Asset statuses that mean "the money is still to come" — what an arriving
# entry can settle (see expected_money / add_entry).
EXPECTED_STATUSES = ("Active", "Pending")

KIND_LABELS = {
    "sale": "Sale",
    "deposit": "Deposit",
    "bill": "Bill paid",
    "income": "Money received",
    "interest": "Interest",
    "fee": "Bank fee",
    "adjustment": "Correction",
    "other": "Other",
}

# Estate categories: how the liquidator's final account to the heirs and the
# estate's last tax returns group the money. One fixed list on purpose — the
# same for every estate, nothing to set up. (key, label, direction, examples)
CATEGORIES = [
    ("sale", "Sale of items", "in", "Furniture, car, tools — filled in from Sold assets"),
    ("accounts", "Bank accounts & investments", "in", "The deceased's accounts, RRSP/TFSA, shares cashed in"),
    ("insurance", "Insurance & pensions", "in", "Life insurance, QPP/RRQ death benefit, pension"),
    ("refunds", "Government refunds & benefits", "in", "Tax refund, GST/QST credit, benefit paid after death"),
    ("interest", "Interest", "in", "Interest paid by the bank"),
    ("other_in", "Other money in", "in", "Anything else received"),
    ("funeral", "Funeral & burial", "out", "Funeral home, cemetery, reception"),
    ("property", "House, car & utilities", "out", "Hydro, internet, home and car insurance, school/property tax, repairs, keys"),
    ("professional", "Legal & professional fees", "out", "Notary, accountant, lawyer, appraiser, RDPRM, death certificate"),
    ("selling", "Selling costs", "out", "DuProprio, real-estate agent, ads"),
    ("taxes", "Taxes owed", "out", "Final income tax returns, estate tax returns"),
    ("debts", "Debts of the deceased", "out", "Credit cards, loans, unpaid bills before the death"),
    ("bank_fees", "Bank fees", "out", "Account fees, counter or cheque fees"),
    ("other_out", "Other expenses", "out", "Stamps, mileage, anything else"),
]
CATEGORY_LABELS = {key: label for key, label, _, _ in CATEGORIES}
CATEGORY_DIRECTION = {key: direction for key, _, direction, _ in CATEGORIES}
# Categories a bill (liability) can have: everything going out.
BILL_CATEGORIES = [(key, label) for key, label, direction, _ in CATEGORIES if direction == "out"]

# The category an entry gets from its kind alone; bills take their
# liability's category, manual lines the one the user picked.
_KIND_CATEGORY = {"sale": "sale", "fee": "bank_fees", "interest": "interest"}

# "Add entry" choices that aren't a category: a correction just fixes a
# balance (e.g. a typo the bank check revealed) and counts as neither.
CORRECTIONS = [("correction_in", "Correction — adds money"), ("correction_out", "Correction — removes money")]


def entry_choice(choice):
    """An "Add entry" pick -> (kind, category, sign)."""
    if choice == "correction_in":
        return "adjustment", None, 1
    if choice == "correction_out":
        return "adjustment", None, -1
    if choice not in CATEGORY_LABELS or choice == "sale":
        raise MoneyError("Choose a category for this entry.")
    sign = 1 if CATEGORY_DIRECTION[choice] == "in" else -1
    kind = {"interest": "interest", "bank_fees": "fee"}.get(choice, "income" if sign > 0 else "bill")
    return kind, choice, sign


def entry_label(entry):
    """What the register shows in its Category column."""
    if entry["category"]:
        return CATEGORY_LABELS.get(entry["category"], entry["category"])
    if entry["kind"] == "bill":
        return "No category"
    return KIND_LABELS.get(entry["kind"], entry["kind"])


def parse_bill_category(value):
    """A category from a liability form: '' (not set) or an outgoing category key."""
    value = (value or "").strip()
    if not value:
        return None
    if value not in dict(BILL_CATEGORIES):
        raise MoneyError("Choose a category for this bill.")
    return value


class MoneyError(ValueError):
    """A user-facing refusal (bad amount, locked entry, unbalanced check)."""


# ---------------------------------------------------------------------------
# Amount helpers
# ---------------------------------------------------------------------------

def _to_cents(text):
    """'1 000,50', '$1,000.50', '1000.5', '-12' -> signed cents."""
    raw = (text or "").strip().replace("$", "").replace(" ", "").replace(" ", "")
    if raw.count(",") == 1 and "." not in raw and len(raw.split(",")[1]) <= 2:
        raw = raw.replace(",", ".")  # French decimal comma: 1000,50
    raw = raw.replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise MoneyError(f"“{text}” is not a valid amount.")
    if not value.is_finite():
        raise MoneyError(f"“{text}” is not a valid amount.")
    return int((value * 100).quantize(Decimal("1")))


def parse_money(text):
    """An amount typed by the user for a movement. Must be > 0."""
    cents = _to_cents(text)
    if cents <= 0:
        raise MoneyError("The amount must be greater than zero.")
    return cents


def parse_balance(text):
    """A balance read off a bank printout: zero or negative allowed."""
    return _to_cents(text)


def real_to_cents(value):
    """An assets/liabilities REAL column -> cents, or None if not a number."""
    if value is None or value == "":
        return None
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        return None


def dollars(cents):
    """Cents -> float for the |money filter (display only, never stored)."""
    return None if cents is None else cents / 100


def parse_date(text):
    text = (text or "").strip()
    if not text:
        return date.today().isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise MoneyError(f"“{text}” is not a valid date.")


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def _display_name(user):
    return (user["name"] or user["email"].split("@")[0]) if user else "Unknown"


def bank_account(db):
    row = db.execute("SELECT * FROM money_accounts WHERE kind = 'bank' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        db.execute("INSERT INTO money_accounts (name, kind) VALUES ('Estate bank account', 'bank')")
        row = db.execute("SELECT * FROM money_accounts WHERE kind = 'bank' ORDER BY id LIMIT 1").fetchone()
    return row


def held_account(db, user_id):
    row = db.execute(
        "SELECT * FROM money_accounts WHERE kind = 'held' AND user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        user = db.execute("SELECT name, email FROM users WHERE id = ?", (user_id,)).fetchone()
        db.execute(
            "INSERT INTO money_accounts (name, kind, user_id) VALUES (?, 'held', ?)",
            (f"Cash held by {_display_name(user)}", user_id),
        )
        row = db.execute(
            "SELECT * FROM money_accounts WHERE kind = 'held' AND user_id = ?", (user_id,)
        ).fetchone()
    return row


def account_label(db, account):
    if account["kind"] == "bank":
        return "Estate bank account"
    user = db.execute("SELECT name, email FROM users WHERE id = ?", (account["user_id"],)).fetchone()
    return f"Cash held by {_display_name(user)}"


def bank_balance(db):
    """Book balance of the estate account, or None until the opening balance is set."""
    bank = bank_account(db)
    if bank["opening_balance_cents"] is None:
        return None
    moved = db.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM money_entries WHERE account_id = ? AND entry_date > ?",
        (bank["id"], bank["opening_date"]),
    ).fetchone()[0]
    return bank["opening_balance_cents"] + moved


def held_balance(db, user_id):
    row = db.execute(
        "SELECT COALESCE(SUM(e.amount_cents), 0) FROM money_entries e "
        "JOIN money_accounts a ON a.id = e.account_id WHERE a.kind = 'held' AND a.user_id = ?",
        (user_id,),
    ).fetchone()
    return row[0]


def held_balances(db):
    """One row per person with a held account: user_id, name, account_id, balance_cents."""
    rows = db.execute(
        """
        SELECT a.id AS account_id, a.user_id, u.name, u.email,
               COALESCE((SELECT SUM(amount_cents) FROM money_entries WHERE account_id = a.id), 0) AS balance_cents
        FROM money_accounts a LEFT JOIN users u ON u.id = a.user_id
        WHERE a.kind = 'held' AND EXISTS (SELECT 1 FROM money_entries WHERE account_id = a.id)
        ORDER BY balance_cents DESC, u.name
        """
    ).fetchall()
    return [
        {
            "account_id": r["account_id"],
            "user_id": r["user_id"],
            "name": (r["name"] or (r["email"] or "").split("@")[0]) or "Unknown",
            "balance_cents": r["balance_cents"],
        }
        for r in rows
    ]


def net_estate(db):
    """Estimated net estate — what the heirs would share today — in cents.

    Every dollar is counted once. An asset counts only while it is still a
    thing to sell: once Sold its money is in someone's held cash or the bank,
    and once IN_BANK the bank put it straight into the estate account. Bills
    count what is still owed on them; Paid and Cancelled ones count nothing.
    A negative held balance (someone paid a bill with their own money) is
    owed back to them, so it lowers the total.
    """
    items = sum(
        real_to_cents(r[0]) or 0
        for r in db.execute(
            "SELECT COALESCE(NULLIF(sale_price, ''), estimated_value) FROM assets WHERE status NOT IN ('Sold', ?)",
            (IN_BANK,),
        )
    )
    bank = bank_balance(db)
    held = sum(h["balance_cents"] for h in held_balances(db))
    bills = 0
    for r in db.execute(
        "SELECT l.amount, COALESCE((SELECT -SUM(amount_cents) FROM money_entries "
        "WHERE liability_id = l.id AND kind = 'bill'), 0) AS paid_cents "
        "FROM liabilities l WHERE l.status NOT IN ('Paid', 'Cancelled')"
    ):
        bills += max(0, (real_to_cents(r["amount"]) or 0) - r["paid_cents"])
    return {
        "items_cents": items,
        "bank_cents": bank,  # None until the starting balance is set
        "held_cents": held,
        "bills_cents": bills,
        "total_cents": items + (bank or 0) + held - bills,
    }


def payment_sources(db, users):
    """(value, label) choices for 'paid from': the bank, then each person's cash.

    Values are 'bank' / 'user:<id>' rather than account ids, so a person's
    held account is only created once money actually moves (resolve_account),
    not just because a form listed them.
    """
    choices = [("bank", "Estate bank account")]
    for u in users:
        choices.append((f"user:{u['id']}", f"Cash held by {_display_name(u)}"))
    return choices


def resolve_account(db, value):
    """'bank' | 'user:<id>' from a form -> account id (creating a held account if needed)."""
    value = (value or "").strip()
    if value == "bank":
        return bank_account(db)["id"]
    if value.startswith("user:") and value[5:].isdigit():
        user_id = int(value[5:])
        if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            return held_account(db, user_id)["id"]
    raise MoneyError("Choose which account the money moved in.")


def set_opening_balance(db, cents, on_date):
    bank = bank_account(db)
    if db.execute("SELECT 1 FROM reconciliations WHERE account_id = ? LIMIT 1", (bank["id"],)).fetchone():
        raise MoneyError("The opening balance can't change after a bank check has been completed.")
    db.execute(
        "UPDATE money_accounts SET opening_balance_cents = ?, opening_date = ? WHERE id = ?",
        (cents, on_date, bank["id"]),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def _insert(db, account_id, cents, kind, on_date, description, actor_id,
            asset_id=None, liability_id=None, transfer_id=None, category=None):
    category = category or _KIND_CATEGORY.get(kind)
    cur = db.execute(
        "INSERT INTO money_entries "
        "(entry_date, account_id, amount_cents, kind, category, description, asset_id, liability_id, transfer_id, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (on_date, account_id, cents, kind, category, description, asset_id, liability_id, transfer_id, actor_id),
    )
    return cur.lastrowid


def sync_sale_entry(db, asset_id, actor_id=None):
    """Keep the 'sale' entry for one asset in line with the asset row.

    Sold + a price + a seller -> one +sale entry on the seller's held account.
    Anything else (not sold, no price, deleted asset) -> no sale entry.
    Call after every asset create/edit/delete; the caller commits.
    """
    asset = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    existing = db.execute(
        "SELECT * FROM money_entries WHERE kind = 'sale' AND asset_id = ?", (asset_id,)
    ).fetchone()
    cents = real_to_cents(asset["sale_price"]) if asset else None
    wanted = asset is not None and asset["status"] == "Sold" and asset["sold_by"] and cents and cents > 0

    if not wanted:
        if existing:
            db.execute("DELETE FROM money_entries WHERE id = ?", (existing["id"],))
        return

    account = held_account(db, asset["sold_by"])
    description = f"Sold: {asset['name']}"
    if existing:
        db.execute(
            "UPDATE money_entries SET account_id = ?, amount_cents = ?, category = 'sale', description = ? WHERE id = ?",
            (account["id"], cents, description, existing["id"]),
        )
    else:
        _insert(db, account["id"], cents, "sale", date.today().isoformat(), description, actor_id,
                asset_id=asset_id)


def record_deposit(db, user_id, cents, on_date, description, actor_id):
    held = held_account(db, user_id)
    bank = bank_account(db)
    transfer_id = uuid.uuid4().hex
    note = description or "Deposit to the estate account"
    _insert(db, held["id"], -cents, "deposit", on_date, note, actor_id, transfer_id=transfer_id)
    _insert(db, bank["id"], cents, "deposit", on_date, note, actor_id, transfer_id=transfer_id)
    db.commit()


def record_payment(db, account_id, cents, on_date, description, actor_id, liability_id=None, category=None):
    _check_account(db, account_id)
    if liability_id and category:
        set_liability_category(db, liability_id, category)
    _insert(db, account_id, -cents, "bill", on_date, description, actor_id,
            liability_id=liability_id, category=category)
    if liability_id:
        db.execute(
            "UPDATE liabilities SET status = 'Paid', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (liability_id,),
        )
    db.commit()


def add_entry(db, account_id, choice, cents, on_date, description, actor_id, asset_id=None):
    """A manual line from "Add entry": `choice` is a category key or a correction.

    With `asset_id`, the money is the arrival of an item the estate was
    expecting (a cheque, a benefit): the entry is tied to it and the item moves
    to IN_BANK in the same step, so it is never counted twice. Only money
    coming into the bank account can settle an item.
    """
    kind, category, sign = entry_choice(choice)
    _check_account(db, account_id)
    if asset_id is not None:
        asset = db.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()
        account = db.execute("SELECT kind FROM money_accounts WHERE id = ?", (account_id,)).fetchone()
        if asset is None or asset["status"] not in EXPECTED_STATUSES or sign < 0 or account["kind"] != "bank":
            raise MoneyError("That item can't be marked as received by this entry.")
    _insert(db, account_id, sign * cents, kind, on_date, description, actor_id,
            asset_id=asset_id, category=category)
    if asset_id is not None:
        db.execute(
            "UPDATE assets SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (IN_BANK, asset_id),
        )
    db.commit()


def expected_money(db, cents=None):
    """Items whose money has not arrived yet: what "Add entry" can offer to settle.

    Active or Pending, with a value, and not already tied to a money entry.
    Sold items are out (their sale is its own entry) and so are items already
    In bank account. With `cents`, only those worth exactly that amount.
    """
    rows = db.execute(
        "SELECT a.id, a.name, a.status, COALESCE(NULLIF(a.sale_price, ''), a.estimated_value) AS value "
        "FROM assets a WHERE a.status IN ('Active', 'Pending') "
        "AND NOT EXISTS (SELECT 1 FROM money_entries e WHERE e.asset_id = a.id AND e.kind != 'sale') "
        "ORDER BY a.name"
    ).fetchall()
    out = []
    for r in rows:
        value = real_to_cents(r["value"])
        if not value or value <= 0 or (cents is not None and value != cents):
            continue
        out.append({"id": r["id"], "name": r["name"], "status": r["status"], "cents": value})
    return out


def settle_candidates(db, account_id, choice, cents):
    """Items an "Add entry" might be the arrival of — only for money coming into the bank."""
    account = db.execute("SELECT kind FROM money_accounts WHERE id = ?", (account_id,)).fetchone()
    kind, _category, sign = entry_choice(choice)
    if account is None or account["kind"] != "bank" or sign < 0 or kind != "income":
        return []
    return expected_money(db, cents)


def set_liability_category(db, liability_id, category):
    """A bill's category, copied onto its payments so they always agree. Caller commits."""
    db.execute("UPDATE liabilities SET category = ? WHERE id = ?", (category, liability_id))
    db.execute(
        "UPDATE money_entries SET category = ? WHERE liability_id = ? AND kind = 'bill'",
        (category, liability_id),
    )


def category_totals(db):
    """Money in and out by category, across the bank and every person's cash.

    Deposits and corrections are left out: they move money, they don't earn
    or spend it. Returns {"in": [...], "out": [...], "uncategorized": {...}}
    with rows {key, label, cents, count}; amounts are positive.
    """
    rows = db.execute(
        "SELECT category, kind, SUM(amount_cents) AS cents, COUNT(*) AS n FROM money_entries "
        "WHERE kind NOT IN ('deposit', 'adjustment') GROUP BY category, kind"
    ).fetchall()
    by_key = {}
    missing = {"cents": 0, "count": 0}
    for r in rows:
        if r["category"] in CATEGORY_LABELS:
            item = by_key.setdefault(r["category"], {"cents": 0, "count": 0})
        else:
            item = missing
        item["cents"] += r["cents"]
        item["count"] += r["n"]
    out = {"in": [], "out": [], "uncategorized": missing}
    for key, label, direction, _ in CATEGORIES:
        if key in by_key:
            total = by_key[key]
            out[direction].append({
                "key": key, "label": label, "count": total["count"],
                "cents": total["cents"] if direction == "in" else -total["cents"],
            })
    missing["cents"] = abs(missing["cents"])
    return out


def backfill_categories(db, log=lambda message: None):
    """Give existing entries the category their kind or bill implies. Safe to re-run."""
    changed = 0
    for kind, category in _KIND_CATEGORY.items():
        changed += db.execute(
            "UPDATE money_entries SET category = ? WHERE kind = ? AND category IS NULL", (category, kind)
        ).rowcount
    changed += db.execute(
        "UPDATE money_entries SET category = (SELECT l.category FROM liabilities l WHERE l.id = money_entries.liability_id) "
        "WHERE kind = 'bill' AND category IS NULL AND liability_id IS NOT NULL"
    ).rowcount
    db.commit()
    if changed:
        log(f"  [OK] Money categories: {changed} existing entries categorised.")


def _check_account(db, account_id):
    if not db.execute("SELECT 1 FROM money_accounts WHERE id = ?", (account_id,)).fetchone():
        raise MoneyError("Unknown account.")


def delete_entry(db, entry_id):
    entry = db.execute("SELECT * FROM money_entries WHERE id = ?", (entry_id,)).fetchone()
    if entry is None:
        raise MoneyError("That entry no longer exists.")
    if entry["kind"] == "sale":
        raise MoneyError("Sales follow their asset — edit or un-sell the asset instead.")
    if entry["kind"] == "deposit" and not entry["transfer_id"]:
        raise MoneyError("This money was deposited before the starting balance and is already in it.")
    group = [entry]
    if entry["transfer_id"]:
        group = db.execute(
            "SELECT * FROM money_entries WHERE transfer_id = ?", (entry["transfer_id"],)
        ).fetchall()
    if any(e["reconciliation_id"] for e in group):
        raise MoneyError("This entry was checked against the bank and is locked.")
    for e in group:
        db.execute("DELETE FROM money_entries WHERE id = ?", (e["id"],))
    if entry["asset_id"] and entry["kind"] != "sale":
        # The money is gone again, so the item is "expected, not received" —
        # otherwise it would drop out of the net estate with nothing replacing it.
        if not db.execute(
            "SELECT 1 FROM money_entries WHERE asset_id = ? AND kind != 'sale' LIMIT 1", (entry["asset_id"],)
        ).fetchone():
            db.execute(
                "UPDATE assets SET status = 'Pending', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE id = ? AND status = ?",
                (entry["asset_id"], IN_BANK),
            )
    if entry["liability_id"]:
        still_paid = db.execute(
            "SELECT 1 FROM money_entries WHERE liability_id = ? LIMIT 1", (entry["liability_id"],)
        ).fetchone()
        if not still_paid:
            db.execute(
                "UPDATE liabilities SET status = 'Unpaid', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE id = ? AND status = 'Paid'",
                (entry["liability_id"],),
            )
    db.commit()


def account_entries(db, account_id):
    """Entries oldest -> newest with a running balance, returned newest first.

    For the bank account, entries on/before the opening date are flagged
    `in_opening` and excluded from the running balance (already counted).
    """
    account = db.execute("SELECT * FROM money_accounts WHERE id = ?", (account_id,)).fetchone()
    rows = db.execute(
        """
        SELECT e.*, l.creditor AS liability_creditor, a.name AS asset_name,
               r.statement_date AS reconciled_on
        FROM money_entries e
        LEFT JOIN liabilities l ON l.id = e.liability_id
        LEFT JOIN assets a ON a.id = e.asset_id
        LEFT JOIN reconciliations r ON r.id = e.reconciliation_id
        WHERE e.account_id = ?
        ORDER BY e.entry_date ASC, e.id ASC
        """,
        (account_id,),
    ).fetchall()
    is_bank = account["kind"] == "bank"
    opening_date = account["opening_date"] if is_bank else None
    running = (account["opening_balance_cents"] or 0) if is_bank else 0
    out = []
    for r in rows:
        item = dict(r)
        item["kind_label"] = KIND_LABELS.get(r["kind"], r["kind"])
        item["category_label"] = entry_label(r)
        item["in_opening"] = bool(is_bank and opening_date and r["entry_date"] <= opening_date)
        if not item["in_opening"]:
            running += r["amount_cents"]
        item["running_cents"] = None if item["in_opening"] else running
        out.append(item)
    out.reverse()
    return out


def liability_payments(db, liability_id):
    rows = db.execute(
        "SELECT e.*, a.kind AS account_kind, a.user_id AS account_user_id "
        "FROM money_entries e JOIN money_accounts a ON a.id = e.account_id "
        "WHERE e.liability_id = ? ORDER BY e.entry_date, e.id",
        (liability_id,),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        acct = db.execute("SELECT * FROM money_accounts WHERE id = ?", (r["account_id"],)).fetchone()
        item["account_label"] = account_label(db, acct)
        out.append(item)
    return out


def needs_attention(db):
    """Sold assets the money ledger can't count: no price, or a price but no seller.

    A price of 0 is a deliberate answer — the item was given away — so it
    is not flagged.
    """
    return db.execute(
        "SELECT id, name, sale_price, sold_by FROM assets "
        "WHERE status = 'Sold' AND (sale_price IS NULL OR sale_price = '' OR (sale_price > 0 AND sold_by IS NULL)) "
        "ORDER BY name"
    ).fetchall()


# ---------------------------------------------------------------------------
# Statements: the bank paper behind each confirmed balance
# ---------------------------------------------------------------------------
# A document row linked with linked_entity_type 'opening_balance' (id = the
# bank account) backs the starting balance; 'reconciliation' (id = the check)
# backs a bank check. Either a fresh upload or a PDF already in Documents.

def linked_document(db, entity_type, entity_id):
    return db.execute(
        "SELECT id, filename FROM documents WHERE linked_entity_type = ? AND linked_entity_id = ? "
        "AND filepath <> '' ORDER BY id DESC LIMIT 1",
        (entity_type, entity_id),
    ).fetchone()


def unlinked_documents(db):
    """PDFs in Documents not attached to anything yet — candidates for a statement."""
    return db.execute(
        "SELECT id, filename, created_at FROM documents "
        "WHERE COALESCE(linked_entity_type, '') = '' AND filepath <> '' ORDER BY created_at DESC, id DESC"
    ).fetchall()


def check_linkable(db, document_id):
    row = db.execute(
        "SELECT COALESCE(linked_entity_type, '') AS linked FROM documents WHERE id = ? AND filepath <> ''",
        (document_id,),
    ).fetchone()
    if row is None:
        raise MoneyError("That document no longer exists.")
    if row["linked"]:
        raise MoneyError("That document is already attached to something else.")


def link_document(db, document_id, entity_type, entity_id):
    """Attach a PDF already in Documents. Caller commits."""
    check_linkable(db, document_id)
    db.execute(
        "UPDATE documents SET linked_entity_type = ?, linked_entity_id = ? WHERE id = ?",
        (entity_type, entity_id, document_id),
    )


# ---------------------------------------------------------------------------
# Bank checks (reconciliation)
# ---------------------------------------------------------------------------

def last_reconciliation(db):
    bank = bank_account(db)
    return db.execute(
        "SELECT * FROM reconciliations WHERE account_id = ? ORDER BY statement_date DESC, id DESC LIMIT 1",
        (bank["id"],),
    ).fetchone()


def reconcile_start_cents(db):
    """Balance already agreed with the bank: last check, else the opening balance."""
    last = last_reconciliation(db)
    if last:
        return last["statement_balance_cents"]
    return bank_account(db)["opening_balance_cents"]


def unchecked_entries(db, up_to=None):
    """Bank entries after the opening date not yet ticked off against a statement."""
    bank = bank_account(db)
    sql = (
        "SELECT * FROM money_entries WHERE account_id = ? AND reconciliation_id IS NULL "
        "AND entry_date > ?"
    )
    params = [bank["id"], bank["opening_date"] or ""]
    if up_to:
        sql += " AND entry_date <= ?"
        params.append(up_to)
    rows = db.execute(sql + " ORDER BY entry_date, id", params).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["kind_label"] = KIND_LABELS.get(r["kind"], r["kind"])
        item["category_label"] = entry_label(r)
        out.append(item)
    return out


def finish_reconciliation(db, statement_date, statement_cents, entry_ids, actor_id):
    bank = bank_account(db)
    if bank["opening_balance_cents"] is None:
        raise MoneyError("Set up the estate account's opening balance first.")
    if statement_date < bank["opening_date"]:
        raise MoneyError("The statement date is before the opening balance date.")
    last = last_reconciliation(db)
    if last and statement_date < last["statement_date"]:
        raise MoneyError(f"The statement date is before the last bank check ({last['statement_date']}).")

    eligible = {e["id"]: e for e in unchecked_entries(db, up_to=statement_date)}
    chosen = [eligible[i] for i in entry_ids if i in eligible]
    calculated = reconcile_start_cents(db) + sum(e["amount_cents"] for e in chosen)
    difference = statement_cents - calculated
    if difference != 0:
        raise MoneyError(
            f"Not balanced yet: the statement shows {statement_cents / 100:,.2f} $ but the ticked lines "
            f"give {calculated / 100:,.2f} $ (difference {difference / 100:,.2f} $)."
        )
    cur = db.execute(
        "INSERT INTO reconciliations (account_id, statement_date, statement_balance_cents, completed_by) "
        "VALUES (?, ?, ?, ?)",
        (bank["id"], statement_date, statement_cents, actor_id),
    )
    rec_id = cur.lastrowid
    for e in chosen:
        db.execute("UPDATE money_entries SET reconciliation_id = ? WHERE id = ?", (rec_id, e["id"]))
    db.commit()
    return rec_id


def undo_last_reconciliation(db):
    last = last_reconciliation(db)
    if last is None:
        raise MoneyError("There is no bank check to undo.")
    db.execute("UPDATE money_entries SET reconciliation_id = NULL WHERE reconciliation_id = ?", (last["id"],))
    db.execute("DELETE FROM reconciliations WHERE id = ?", (last["id"],))
    db.commit()


def reconciliations(db):
    bank = bank_account(db)
    return db.execute(
        """
        SELECT r.*, u.name AS completed_by_name, u.email AS completed_by_email,
               (SELECT COUNT(*) FROM money_entries WHERE reconciliation_id = r.id) AS entry_count,
               (SELECT d.id FROM documents d WHERE d.linked_entity_type = 'reconciliation'
                  AND d.linked_entity_id = r.id AND d.filepath <> '' ORDER BY d.id DESC LIMIT 1) AS document_id
        FROM reconciliations r LEFT JOIN users u ON u.id = r.completed_by
        WHERE r.account_id = ? ORDER BY r.statement_date DESC, r.id DESC
        """,
        (bank["id"],),
    ).fetchall()


# ---------------------------------------------------------------------------
# One-time conversion from the old Sold / Transferred statuses
# ---------------------------------------------------------------------------

def backfill_from_assets(db, log=lambda message: None):
    """Turn the old per-asset tracking into entries, once per database.

    Sold        -> +sale on the seller's held account (they still hold the cash)
    Transferred -> +sale, plus a -deposit on the held side only: that money
                   reached the bank before the ledger started, so it is already
                   inside the opening balance the user will enter.
    Then every 'Transferred' asset becomes 'Sold' (the status is retired).
    Balances afterwards equal the old dashboard "to transfer" figure.
    """
    done = db.execute("SELECT value FROM settings WHERE key = ?", (BACKFILL_FLAG,)).fetchone()
    if done:
        return
    bank_account(db)
    assets = db.execute(
        "SELECT id, name, sale_price, status, sold_by, updated_at FROM assets "
        "WHERE status IN ('Sold', 'Transferred')"
    ).fetchall()
    sales = deposits = 0
    for a in assets:
        asset_id, name, price, status, sold_by, updated_at = (a[0], a[1], a[2], a[3], a[4], a[5])
        cents = real_to_cents(price)
        if not sold_by or not cents or cents <= 0:
            continue
        on_date = (updated_at or date.today().isoformat())[:10]
        held = held_account(db, sold_by)
        _insert(db, held[0], cents, "sale", on_date, f"Sold: {name}", None, asset_id=asset_id)
        sales += 1
        if status == "Transferred":
            _insert(db, held[0], -cents, "deposit", on_date,
                    f"Deposited before the ledger started ({name})", None)
            deposits += 1
    converted = db.execute("UPDATE assets SET status = 'Sold' WHERE status = 'Transferred'").rowcount
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (BACKFILL_FLAG, date.today().isoformat()),
    )
    db.commit()
    log(f"  [OK] Money ledger: {sales} sales and {deposits} past deposits recorded; "
        f"{converted} 'Transferred' assets now 'Sold'.")
