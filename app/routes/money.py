"""Money page: estate bank account, cash held per person, deposits, bills, bank checks.

All the arithmetic lives in app/money.py; these routes only parse forms,
call it, and redirect back with ?saved= / ?error= for the result banner.
"""

from datetime import date
from urllib.parse import urlencode, urlparse

from flask import abort, g, redirect, render_template, request, url_for

from app import money
from app.auth import admin_required
from app.db import get_db
from app.document_storage import attach_documents

# Ledger lines per page on the Money page (see money_overview()).
PAGE_SIZE = 50


def _users(db):
    return db.execute("SELECT id, name, email FROM users ORDER BY name").fetchall()


def _safe_next(default):
    """Only follow same-site relative ?next= targets."""
    target = request.form.get("next") or request.args.get("next") or ""
    parsed = urlparse(target)
    if target.startswith("/") and not target.startswith("//") and not parsed.netloc:
        return target
    return default


def _with_param(url, **params):
    sep = "&" if "?" in url else "?"
    return url + sep + urlencode(params)


def _statement_choice(db):
    """The statement form fields: (uploaded files, existing document id). Validated, nothing saved yet."""
    files = [f for f in request.files.getlist("statement_file") if f and f.filename]
    document_id = None if files else request.form.get("statement_document", type=int)
    if document_id:
        money.check_linkable(db, document_id)
    return files, document_id


def _attach_statement(db, choice, entity_type, entity_id):
    files, document_id = choice
    if files:
        attach_documents(db, entity_type, entity_id, files)
    elif document_id:
        money.link_document(db, document_id, entity_type, entity_id)
        db.commit()
    return bool(files or document_id)


def register(app):
    @app.route("/app/money")
    def money_overview():
        db = get_db()
        bank = money.bank_account(db)
        db.commit()  # bank_account() may have created the row

        person = request.args.get("person", type=int)
        month = request.args.get("month", "")
        if person:
            account = money.held_account(db, person)
            db.commit()
        else:
            account = bank
        entries = money.account_entries(db, account["id"])
        months = sorted({e["entry_date"][:7] for e in entries}, reverse=True)
        if month:
            entries = [e for e in entries if e["entry_date"].startswith(month)]

        # Running balances above are computed over the whole ledger, so slicing
        # a page out of it keeps every balance correct. Only the HTML is bounded.
        total = len(entries)
        total_pages = max(1, -(-total // PAGE_SIZE))
        page = min(max(1, request.args.get("page", 1, type=int) or 1), total_pages)
        first_row = (page - 1) * PAGE_SIZE + 1 if total else 0
        last_row = min(page * PAGE_SIZE, total)
        entries = entries[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        last_check = money.last_reconciliation(db)
        return render_template(
            "blocks/money_overview.html",
            bank=bank,
            bank_balance=money.bank_balance(db),
            held=money.held_balances(db),
            account=account,
            account_label=money.account_label(db, account),
            person=person,
            entries=entries,
            page=page,
            total_pages=total_pages,
            total=total,
            first_row=first_row,
            last_row=last_row,
            months=months,
            month=month,
            last_check=last_check,
            unchecked_count=len(money.unchecked_entries(db)) if bank["opening_balance_cents"] is not None else 0,
            checks=money.reconciliations(db),
            opening_document=money.linked_document(db, "opening_balance", bank["id"]),
            unlinked_documents=money.unlinked_documents(db),
            totals=money.category_totals(db),
            needs_attention=money.needs_attention(db),
            users=_users(db),
            today=date.today().isoformat(),
            saved=request.args.get("saved"),
            error=request.args.get("error"),
        )

    @app.route("/app/money/setup", methods=["POST"])
    @admin_required
    def money_setup():
        db = get_db()
        try:
            cents = money.parse_balance(request.form.get("opening_balance"))
            on_date = money.parse_date(request.form.get("opening_date"))
            statement = _statement_choice(db)
            money.set_opening_balance(db, cents, on_date)
            attached = _attach_statement(db, statement, "opening_balance", money.bank_account(db)["id"])
        except money.MoneyError as exc:
            return redirect(url_for("money_overview", error=str(exc)))
        return redirect(url_for("money_overview", saved="Opening balance saved" + (" with its statement." if attached else ".")))

    @app.route("/app/money/setup/statement", methods=["POST"])
    @admin_required
    def money_opening_statement():
        db = get_db()
        try:
            if not _attach_statement(db, _statement_choice(db), "opening_balance", money.bank_account(db)["id"]):
                raise money.MoneyError("Choose a PDF to upload or a document from the list.")
        except money.MoneyError as exc:
            return redirect(url_for("money_overview", error=str(exc)))
        return redirect(url_for("money_overview", saved="Statement attached to the starting balance."))

    @app.route("/app/money/deposit", methods=["POST"])
    @admin_required
    def money_deposit():
        db = get_db()
        back = _safe_next(url_for("money_overview"))
        try:
            user_id = request.form.get("user_id", type=int) or g.user["id"]
            if not db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
                raise money.MoneyError("Unknown person.")
            cents = money.parse_money(request.form.get("amount"))
            on_date = money.parse_date(request.form.get("entry_date"))
            money.record_deposit(db, user_id, cents, on_date, request.form.get("description", "").strip(), g.user["id"])
        except money.MoneyError as exc:
            return redirect(_with_param(back, error=str(exc)))
        return redirect(_with_param(back, saved=f"Deposit of {cents / 100:,.2f} $ recorded."))

    @app.route("/app/money/entry/new", methods=["GET", "POST"])
    @admin_required
    def money_new_entry():
        db = get_db()
        back = _safe_next(url_for("money_overview"))
        sources = money.payment_sources(db, _users(db))
        form = request.form if request.method == "POST" else None
        source = request.form if form else request.args
        error = None

        # An item this money is for: chosen on the asset page ("Money received")
        # or confirmed on the "Is this money you were expecting?" step.
        link_id = source.get("link_asset", type=int)
        linked = db.execute("SELECT id, name FROM assets WHERE id = ?", (link_id,)).fetchone() if link_id else None

        if request.method == "POST":
            try:
                account_id = money.resolve_account(db, request.form.get("account_id"))
                choice = request.form.get("category", "")
                cents = money.parse_money(request.form.get("amount"))
                on_date = money.parse_date(request.form.get("entry_date"))
                description = request.form.get("description", "").strip()
                if not description:
                    raise money.MoneyError("Add a short description (e.g. “Insurance cheque”).")

                decision = request.form.get("decision", "")        # "link" | "separate" | ""
                asset_id = linked["id"] if (linked and decision != "separate") else None
                if decision == "link" and asset_id is None:
                    candidates = money.settle_candidates(db, account_id, choice, cents)
                    return render_template(
                        "blocks/money_entry_match.html", matches=candidates, form=request.form,
                        amount_cents=cents, next_url=back,
                        account_text=dict(sources).get(request.form.get("account_id"), "the estate account"),
                        error="Choose which item this money is for.",
                    ), 400
                if asset_id is None and decision != "separate":
                    candidates = money.settle_candidates(db, account_id, choice, cents)
                    if candidates:
                        # Nothing is written yet: ask first, in plain words.
                        return render_template(
                            "blocks/money_entry_match.html", matches=candidates, form=request.form,
                            amount_cents=cents, next_url=back,
                            account_text=dict(sources).get(request.form.get("account_id"), "the estate account"),
                            error=None,
                        )

                money.add_entry(db, account_id, choice, cents, on_date, description, g.user["id"],
                                asset_id=asset_id)
                note = "Entry added."
                if asset_id is not None:
                    note = f"Entry added — “{linked['name']}” is now marked In bank account."
                return redirect(_with_param(back, saved=note))
            except money.MoneyError as exc:
                error = str(exc)

        return render_template(
            "blocks/money_entry_form.html",
            sources=sources,
            categories=money.CATEGORIES,
            corrections=money.CORRECTIONS,
            form=form,
            prefill={
                "amount": request.args.get("amount", ""),
                "description": request.args.get("description", ""),
                "entry_date": request.args.get("entry_date", ""),
            },
            linked=linked,
            selected_account=request.args.get("account", ""),
            selected_category=request.args.get("category", ""),
            next_url=back,
            today=date.today().isoformat(),
            error=error,
        ), (400 if error else 200)

    @app.route("/app/money/entries/<int:entry_id>/delete", methods=["POST"])
    @admin_required
    def money_delete_entry(entry_id):
        db = get_db()
        back = _safe_next(url_for("money_overview"))
        try:
            money.delete_entry(db, entry_id)
        except money.MoneyError as exc:
            return redirect(_with_param(back, error=str(exc)))
        return redirect(_with_param(back, saved="Entry deleted."))

    @app.route("/app/liabilities/<int:item_id>/pay", methods=["POST"])
    @admin_required
    def pay_liability(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM liabilities WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        back = url_for("detail_liability", item_id=item_id)
        try:
            cents = money.parse_money(request.form.get("amount"))
            on_date = money.parse_date(request.form.get("entry_date"))
            account_id = money.resolve_account(db, request.form.get("account_id"))
            category = money.parse_bill_category(request.form.get("category"))
            if category is None:
                raise money.MoneyError("Choose a category for this bill (e.g. House, car & utilities for Hydro).")
            description = f"{item['creditor']}" + (f" — {item['description']}" if item["description"] else "")
            money.record_payment(db, account_id, cents, on_date, description, g.user["id"],
                                 liability_id=item_id, category=category)
        except money.MoneyError as exc:
            return redirect(_with_param(back, error=str(exc)))
        return redirect(_with_param(back, saved=f"Payment of {cents / 100:,.2f} $ recorded."))

    # ---- bank check (reconciliation) ---------------------------------------

    @app.route("/app/money/check", methods=["GET", "POST"])
    def money_check():
        db = get_db()
        bank = money.bank_account(db)
        db.commit()
        if bank["opening_balance_cents"] is None:
            return redirect(url_for("money_overview", error="Set up the estate account's opening balance first."))

        error = None
        if request.method == "POST":
            if not g.user or g.user["role"] != "admin":
                abort(403)
            statement_date = request.form.get("statement_date", "")
            statement_text = request.form.get("statement_balance", "")
            entry_ids = [int(i) for i in request.form.getlist("entry_ids") if i.isdigit()]
            try:
                statement_date = money.parse_date(statement_date)
                statement_cents = money.parse_balance(statement_text)
                statement = _statement_choice(db)
                rec_id = money.finish_reconciliation(db, statement_date, statement_cents, entry_ids, g.user["id"])
                _attach_statement(db, statement, "reconciliation", rec_id)
                return redirect(url_for("money_overview", saved=f"Bank check of {statement_date} completed — everything matches."))
            except money.MoneyError as exc:
                error = str(exc)
                ticked = set(entry_ids)
        else:
            statement_date = request.args.get("date", "")
            statement_text = request.args.get("balance", "")
            ticked = None

        statement_cents = None
        if statement_date and statement_text:
            try:
                statement_date = money.parse_date(statement_date)
                statement_cents = money.parse_balance(statement_text)
            except money.MoneyError as exc:
                error = str(exc)
                statement_cents = None

        candidates = money.unchecked_entries(db, up_to=statement_date) if statement_cents is not None else []
        return render_template(
            "blocks/money_check.html",
            bank=bank,
            last_check=money.last_reconciliation(db),
            start_cents=money.reconcile_start_cents(db),
            statement_date=statement_date or date.today().isoformat(),
            statement_text=statement_text,
            statement_cents=statement_cents,
            candidates=candidates,
            ticked=ticked,
            unlinked_documents=money.unlinked_documents(db),
            error=error,
        ), (400 if error and request.method == "POST" else 200)

    @app.route("/app/money/check/undo", methods=["POST"])
    @admin_required
    def money_check_undo():
        db = get_db()
        try:
            money.undo_last_reconciliation(db)
        except money.MoneyError as exc:
            return redirect(url_for("money_overview", error=str(exc)))
        return redirect(url_for("money_overview", saved="The last bank check was undone; its lines are unlocked."))
