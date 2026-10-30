**English** · [Русский](README.ru.md)

# Home budget

A local home-finance system. It collects purchases from every source **down to individual items**, sorts them into
categories, reconciles them with the bank statement and shows everything in a web interface on `localhost`.
The interface is available in **English and Russian** (Settings → Language).

| Source | What it gives | How it arrives |
|---|---|---|
| Lidl Plus | receipts with items, discounts, payment method | the app's API (sign-in in a Chrome window) |
| Kaufland Card | receipts with items, discounts, shop departments, bottle deposits | the app's API (sign-in in a Chrome window) |
| Biedronka (beta) | PDF receipts, items, discounts | the Moja Biedronka site, session from Chrome via an extension |
| Receipt photos/scans | any shop, cash | a Telegram group or a folder, recognised with Tesseract (+EasyOCR) |
| Gmail | online orders (Allegro, Koleo, DOZ, Media Expert…), subscriptions, payments via PayU/Tpay/Przelewy24 | IMAP, read-only |
| PKO bank | all account transactions, balance, income | Enable Banking (PSD2, read-only) |
| Statement from a file | transactions of any bank: online-banking CSV, MT940, camt.053 | the Bank page or the `bank/inbox/` folder |
| Cash | how much cash you have, cash income and spending | your entries + automatically from the statement and receipts |

Lidl flyers and deals (deal search, Telegram reminders) are a separate module: [lidl-deals/README.md](lidl-deals/README.md) (in Russian).

**Contents:** [Quick start](#quick-start-first-run) · [Installation](#installation) · [Setting up sources](#setting-up-sources) ·
[Regular updates](#regular-updates) · [Interface](#interface) · [How it works](#how-it-works) ·
[Backups](#backups) · [Security](#security) · [Commands](#all-commands) ·
[Troubleshooting](#troubleshooting) · [Project structure](#project-structure)

---

## Quick start (first run)

1. Install **Python 3.13** from [python.org](https://www.python.org/downloads/) — tick
   **“Add python.exe to PATH”** during installation. Signing in to Lidl, Kaufland and the bank needs **Google Chrome**,
   receipt photos need **Tesseract** (see [Installation](#installation)).
2. Open a terminal in the project folder (in Explorer: right-click the folder → “Open in Terminal”) and run:
   ```
   pip install -r requirements.txt
   copy config.ini.example config.ini
   python budget.py serve
   ```
   A browser opens at `http://127.0.0.1:8765`. The server listens only on this computer. Stop it with `Ctrl+C`.
   Until sources are connected the pages are empty — the database (`data/budget.db`) is created automatically.
3. Connect the sources you need — each once, all optional: [Setting up sources](#setting-up-sources).
   You can start with one, e.g. Lidl Plus. What's connected and what isn't is shown on the **Settings** page.
4. Update the data with one command or the “Update all now” button on the Settings page:
   ```
   python budget.py update
   ```
   Daily autostart (Windows Task Scheduler): `python budget.py schedule on 07:30`.
5. Set a backup password: `python budget.py backup password` — backups are then made during updates.

The interface language follows your browser (Russian browser — Russian, any other — English); change it in
**Settings → Language**. Telegram reminders use the same language.

---

## Installation

1. **Python 3.13** and dependencies:
   ```
   pip install -r requirements.txt
   ```
   Receipt photos are recognised two ways: **Tesseract** (main) and **EasyOCR** (a re-check when the items don't add
   up to the total). EasyOCR is installed with everything else but pulls in PyTorch — **~1–2 GB** and a few minutes
   of installation. If disk space or internet is limited, delete the `easyocr` line from `requirements.txt` before
   installing: everything still works, photos are then recognised by Tesseract only. Install later: `pip install easyocr`.
2. **Google Chrome** — needed to sign in to Lidl, Kaufland and the bank. The matching driver downloads itself
   (an old chromedriver in PATH is ignored).
3. **Tesseract OCR** — for receipt photos:
   ```
   winget install UB-Mannheim.TesseractOCR
   ```
   The script downloads the Polish dictionary to `data/tessdata` on the first recognition.
4. **Config:** copy `config.ini.example` to `config.ini` and fill it in (see below).

### Updating the app
Settings → **“Check for updates”**: if a new version is out on GitHub, you see what's new and an
“Install and restart” button — the app downloads the release, replaces its files, runs `pip install` when there are new
dependencies, and the server restarts by itself (the page reloads). The same from a terminal:
```
python budget.py upgrade            # check and install (asks for confirmation)
python budget.py upgrade --check    # only check
```
- The database, tokens, `config.ini`, backups and inbox folders don't change; before installing — a database copy
  in `data/upgrade/`. A `git clone` installation is updated with `git pull`.
- The server restarts by itself when started with `python budget.py serve` version 1.2.2 or newer
  (`serve` watches the server and brings it back after installation, and also when the app's code changes).
  From an older version update manually once: download the release ZIP and unpack it over the app folder
  (or `git pull`), then `pip install -r requirements.txt`.
- The app goes online for updates only on the button or the command.

---

## Setting up sources

Each source is set up once. Passwords and codes are **always entered by you** — in a browser window
or in the terminal; the scripts don't see or store them. Only access tokens are saved (see [Security](#security)).
Signing in to Lidl, Kaufland and the bank also works without a terminal — the **“refresh token”** button on the Settings page
(the same Chrome window as `login`).

### Lidl Plus
```
python budget.py lidl login     # Chrome window: sign in as in the app (email/phone, password, code)
python budget.py lidl sync      # download new receipts
```
- All receipts from the app are downloaded, the originals go to `data/lidl/raw/`.
- Old receipts come as a list of items, new ones (since spring 2026) as HTML; both formats are parsed.

### Kaufland Card
```
python budget.py kaufland login
python budget.py kaufland sync
```
- Parsing is based on the text of the printed receipt: line discounts (`Pozycje:N`), bottle deposits,
  shop departments (they help with categories), payment method, change, the last card digits.
- Item amounts in the API are already after discounts; the database keeps the undiscounted price + the discount separately.
- The API was found in the open project [Bonfire](https://github.com/Naxter/bonfire); it's unofficial and may break.

### Biedronka (beta)
Turn it on in Settings → Shops → Biedronka and connect the Moja Biedronka site session:
1. Load the **Budget Browser Connector** extension into Chrome once: `chrome://extensions` → “Developer mode” →
   “Load unpacked” → the `tools/browser-connector` folder inside the app folder (the full path is shown
   in Settings, a click copies it).
2. Sign in to Moja Biedronka in that Chrome, click the extension icon → “Connect to budget”.
- The extension takes only the cookies of the biedronka.pl domain and sends them only to this app (`127.0.0.1:8765`);
  it doesn't see your password. The session is kept in `data/biedronka/session.json`, receipt PDFs in `data/biedronka/downloads/`.
- The first download fetches the whole history since 01.01.2023 (month by month), later — from the last downloaded receipt minus 2 weeks.
  Manually: `python budget.py biedronka sync` (`--from 2026-10-01 --to 2026-10-06` — your own period).
- The site session expired — the update step says so; sign in to Biedronka in Chrome and repeat “Connect to budget”.
  When you connected and how long a session lives on average is shown in the source row, like for Lidl and Kaufland.
- This automates the web interface, it's not an API: if Biedronka changes the site, the download may need updating.
  Implementation — [PR #1](https://github.com/vladhaq/home-budget/pull/1) by Alexigorbachev.
- **Biedronka flyers** (a tab on the Deals page): they have no text anywhere — neither on the site nor at aggregators
  (iulotka.pl, twoja-gazetka.pl, blix.pl: images or a selection of a few dozen “hits”). So the food flyers
  (“Codziennie niskie ceny”) are recognised from the page images: each page once (~2–4 s, a flyer — a few
  minutes), the result is kept in `data/biedronka_deals/ocr/`. The main price comes from the per-kg/l price and the
  weight (≈) and is checked against the big price when it could be read. **The data may be inaccurate** — this is marked
  on the tab and in the cards, every deal links to its flyer page. A separate update step “Biedronka flyers (recognition)”
  can be excluded from “Update all” (the switch in Sources) if you only need receipts.

### Receipt photos and scans (Telegram)
In `config.ini`:
```
[telegram]
; keys: https://my.telegram.org -> API development tools
api_id = ...
api_hash = ...
; invite link of the group for receipts
receipts_chat = https://t.me/+...
```
Comments in `config.ini` go only on their own line starting with `;`: at the end of a line they become part of the value.
```
python budget.py telegram       # asks for your phone and Telegram code the first time
```
- Send photos **as a file**, not as a photo — Telegram won't compress it and recognition is more accurate.
- Formats: jpg, jpeg, png, webp, heic, pdf. A PDF with text is read without recognition.
- Recognition: 5 Tesseract variants → a vote on every field → picking the items so the sum matches the receipt
  total. If it doesn't match — a re-check with EasyOCR; one item unreadable — its amount is calculated from the total (≈).
  If it still doesn't match, the receipt is saved marked “⚠ check manually”. Identical products on different lines
  (two fillets) are different items; subtotals and “OPUSTY ŁĄCZNIE” aren't products; Kaufland quantity on a separate
  line (“2SZT x7,99”) is understood even when garbled (“25ZT”, “152T”).
- **How accurate it is** — `python tools/ocrbench.py`: every photo duplicated by an electronic Lidl/Kaufland receipt
  is a test with a known answer (total, amounts and discounts per product, name similarity for Tesseract,
  EasyOCR and the whole program). The more often you duplicate receipts as photos in Telegram, the bigger the test set.
- **A photo of a receipt that already exists** (an electronic Kaufland/Lidl receipt, an email): same total, time ±15 minutes,
  same shop — the photo doesn't create a duplicate but is attached to that purchase (“📷 receipt photo · ✓ matched the photo”
  in the receipt list). If the photo came before the electronic receipt, reconciliation joins them later by itself.
- If the shop name on the photo wasn't readable, it's taken from the matched bank transaction, so your shop rules apply.
- Without Telegram: put the files into `receipts/inbox/` and run `python budget.py photos`.

### Gmail
1. Turn on Google 2-Step Verification and create an **app password**
   (Google Account → Security → 2-Step Verification → App passwords).
2. ```
   python budget.py mail login      # asks for the address and app password; the password goes to Windows Credential Manager
   python budget.py mail headers    # email headers since the start of the statement (fast, little space)
   python budget.py mail senders    # sender statistics: who looks like a shop
   python budget.py mail mark shop allegro.pl zalando.pl    # mark senders
   python budget.py mail bodies     # full emails — only from marked senders
   python budget.py mail parse      # emails -> purchases
   ```
- Sender labels: `shop` — a shop, `sub` — subscription/phone, `pay` — a payment intermediary
  (PayU, Tpay, Przelewy24, Autopay), `ignore` — not needed.
- Mail is opened read-only, emails aren't marked as read.
- Dedicated parsers: Allegro (items, sellers, delivery, Allegro Smart, coupons), Koleo (price from the PDF ticket),
  Stripe receipts (Anthropic etc., converted from euro at the NBP rate). Other shops — generic parsing:
  order number, items (“N x price”, a “Produkt / Ilość / Kwota” table, “brand / name / size /
  Ilość” blocks, IdoSell shops), delivery, order discount (split across items), total; a doctor's appointment is a service.
- Emails of one order (“accepted / packed / delivered”) are joined; intermediary payments are added only if the
  shop has no email of its own with the same amount. The payment description at the intermediary is the item name (an OLX listing),
  and the order number in it (“Opis: 10900000000001”) links the payment to the shop's email.
- Emails are downloaded from the start of the bank statement (and two weeks before it).

### PKO bank (Enable Banking)
The statement is set up for **PKO BP** (bank search and transaction parsing). Enable Banking supports other Polish
banks too, but their transaction format hasn't been checked here — `bank/enablebanking.py` would need work.
Without the bank everything else works, there's just no reconciliation and no Bank page.

1. Register at [enablebanking.com](https://enablebanking.com), Control Panel → **API applications** →
   **Add a new application**: Environment **Production**, Redirect URL exactly `https://localhost/enablebanking`,
   key — “Generate in the browser”.
2. Put the downloaded `<id>.pem` into `data/bank/`.
3. In the application card — **Link accounts** → PKO BP → sign in to iPKO.
4. ```
   python budget.py bank login     # Chrome window: iPKO sign-in and consent; the code is caught from the localhost address
   python budget.py bank sync      # transactions + balance, then reconciliation automatically
   ```
- The consent lasts ~180 days (the date is on the Bank page, in Settings and in `bank status`); then repeat
  `bank login` or press “refresh token” in Settings.
- Restricted mode is free but sees only the accounts linked in Link accounts.

### Statement from a file (any bank, beta)
If the bank isn't connected (or you need history older than the connection, or another account), load the statement as a file:
the “Add files” button on the Bank page, or put the files into `bank/inbox/` — “update all” will parse them
(parsed files move to `bank/inbox/обработано`), or `python budget.py bank import <files>`.
- **Formats:** camt.053 (ISO 20022 XML — the European banking standard), MT940 (field `:86:` in the Polish
  `~20…` and German `?20…` style), online-banking CSV. CSV columns are recognised by the header — Polish, English,
  German names (iPKO, mBank, Millennium, ING, Revolut…); a file without a header — by its content (dates, signed amount,
  balance, text). iPKO details in “Tytuł: …”, “Lokalizacja: Adres: … Miasto: …” cells are collected
  into the description (for a card — the shop and city).
- **Transaction type** (card, BLIK, ATM, transfer, fee…) is mapped to the same codes as for the connected PKO —
  receipt reconciliation, cash and analytics work with file transactions too.
- **No duplicates:** a transaction of the same account for the same amount within 3 days that already came from the bank
  or an earlier file isn't added. A file without an account number, when one account is connected, is treated as an export
  of that account; a file with another account number is a separate account (the balance on the chart and the “total in accounts” is the sum).
- **Details for bank transactions:** through the connection PKO gives BLIK only the transaction number — no shop
  or address. The same transaction in an iPKO export (matched by “Numer referencyjny”, otherwise by amount and date) adds
  the address and payee: “BLIK without a name” becomes “example.nl”, and reconciliation and shop rules recognise it.
  The added data is kept with the bank's response — rebuilding the statement doesn't lose it.
- A copy of a file loaded through the page goes to `data/bank/imports/`.
- Your bank wasn't recognised or was parsed incorrectly — tell me which bank and which format (without your data): I'll add it.

### Cash
The Cash page in the interface. Start with a **count** — how much cash you have now.

---

## Regular updates

```
python budget.py update                 everything in turn (1–2 minutes)
python budget.py update lidl bank       only the chosen steps (reconciliation is added automatically)
python budget.py update status          latest runs: what updated, what broke
python budget.py schedule on 07:30      daily autostart
python budget.py schedule off | status
```

`update` steps in order:

| Step | What it does | If not set up |
|---|---|---|
| `lidl` | new Lidl Plus receipts | skipped |
| `kaufland` | new Kaufland Card receipts | skipped |
| `biedronka` | new Biedronka receipts | skipped |
| `mail` | new emails; full emails of marked senders | skipped |
| `telegram` | new receipt photos from the group | “not set up” — first sign-in needed: `python budget.py telegram` |
| `photos` | files from `receipts/inbox` (after recognition they move to `receipts/inbox/обработано`) | — |
| `bank` | new PKO transactions | skipped |
| `reconcile` | email parsing, bank reconciliation, cash, categories | — |
| `biedronka_flyers` | recognising new Biedronka flyers | skipped while Biedronka is off |
| `deals` | fresh Lidl and Kaufland flyers; Telegram reminders for the watchlist | flyers update; reminders — after `python budget.py telegram` |
| `backup` | a backup, at most once every `backup_every_days` days (`config.ini` `[update]`, 1 by default) | “not set up” — set the password with `backup password` |

- An error in one step doesn't stop the others. Every run is written to the log (tables `update_runs`,
  `update_steps`), shown on the **Settings** page and in `update status`.
- Each source has an “in Update all” switch in Settings: a switched-off source is updated only with its own “update” button
  (for example the long Biedronka flyer recognition if you only need receipts).
- Sign-ins aren't requested here: if a sign-in is outdated, the step ends with an error. For Lidl, Kaufland and the bank the source
  row shows the sign-in date, how long the token has been working, its **average lifetime** (from sign-in until the service refuses,
  over all sign-ins since October 2026) and a **“refresh token”** button: a Chrome window opens, sign in as in the app.
  It isn't part of “Update all”. Mail, Telegram and the backup password are set up once in a terminal (`mail login`,
  `telegram`, `backup password`) — their command is shown in the row. After signing in — “update” in the source row.
- Autostart is the `BudgetUpdate` task in Windows Task Scheduler, it runs without a window (`pythonw`). If the computer
  is off or asleep at that time, the update starts when it's on. Without internet it waits up to 3 minutes.
  While the update runs it asks Windows not to sleep and not to freeze the process (a power request, like a media player) —
  first thing after starting; Windows doesn't override a closed lid — then the update continues after waking up
  (task limit — 12 hours). If an update was interrupted — Windows crashed in sleep or the computer was turned off — it
  repeats at the next Windows sign-in (the task's second trigger; if the scheduled run already finished, this run exits
  right away). The log shows what happened: “went to sleep right after autostart” or “Windows shut down or
  restarted during the update”. A task from an older version — Settings → Autostart → “Save time”.
- Two updates don't run at once: the second one says “already running”.
- New mail senders aren't downloaded by themselves: the mail step marks “!” and lists the new domains with purchase
  emails — look at `mail senders` and mark them (`mail mark shop <domain>`).
- Bank access (PSD2) lasts 180 days: 21 days before the end the bank step and Settings remind you to renew
  (“refresh token” or `bank login`).

Individual commands (`lidl sync`, `bank sync`…) still work; `bank sync` runs reconciliation by itself.

---

## Interface

`python budget.py serve` → `http://127.0.0.1:8765`.

The look is inspired by Telegram: sections on the left as a chat list (with a hint and counters), content on the right.
**Light and dark theme** — the button in the top right corner (follows Windows by default, the choice is remembered).
**Language** — English or Russian, Settings → Language (by default — as your browser).
**Search** in the header — by item name or shop, works on the overview, uncategorized items and bank transactions (Esc — clear).
Clicking “Budget” at the top collapses the section list to icons (again — expands); on a narrow window it's always collapsed, on a phone — a bottom bar. Clicking the selected chart bar again clears the selection — back to “all time”.

**Filters** (on every page except Settings): period, payment method, shop, categories (with search, **you can pick
several** — e.g. “Food” and “Tobacco & vape”: click one after another, the list stays open), source.
The header shows the date of the last purchase, statement and last update (click — Settings).

| Page | What's there |
|---|---|
| **Overview** | **spending, income or both** (a switch above the chart: blue — spent, green — received; a “difference” card). Income — income and incoming transfers from the statement; pick “Income › Scholarship” or “Transfers › Family” in the filter to see them (for “Family” — both directions). Spending by **days / weeks / months / quarters / years** (switch above the chart; for days — a window of your choice: 2 weeks, a month, 2 or 3 months, half a year, a year — the ‹ › arrows move it back; with a period chosen in the filter — the whole period). **Chart type** — bars, a smooth filled line or a **share ring** (remembered): the ring splits the chosen period by category — without a filter by groups, with one category chosen by its subcategories, with several — by them; the total is in the centre (a category below zero, e.g. returned bottle deposits, is listed as “subtracted”), a click on a slice or legend row shows only that category; with “spending and income” — two rings. Click a bar — stats for the period, again — clear; **double-click — drill down** (year → quarters → months → days, week → days; a day — its receipts), with breadcrumbs and “‹ back” on top. Summary, tabs “categories / items / receipts”. **Categories are a tree**: group → subcategories → items (the arrow expands, plus “expand / collapse all”). The **eye** next to a category or subcategory removes it from the chart and summary (e.g. without rent and tuition) — the row is struck out, “hidden: …” and a “show all” button appear above the chart; remembered in the browser. In “items” a name expands all its purchases. In “receipts” a purchase has a bin — **delete** an unfinished payment or a duplicate; a **refund** to the card is shown inside the original purchase (“↩ refund −275 zł”). Transfers and cash withdrawals aren't counted |
| **Bank** | the available balance (minus blocked purchases the bank hasn't settled yet — how much is blocked is on the card) and the balance chart from the statement; income and spending by month; the share of spending backed by receipts; the transaction list by the kinds in the chart legend (income, with receipts, statement only, other card, transfers). Sections collapse (the open ones are remembered). **Any amount in the monthly table is clickable** — the transactions it's made of open below (e.g. income for September); a click on the month name — all transactions of the month. **A transaction expands** with a click on “who / shop”: what's in the statement, where the purchase came from (email, photo, receipt), all items with categories, receipts and orders on those days; for income — how much came from that sender in total |
| **Deals** | tabs of the shops enabled in Settings: **Lidl**, **Kaufland** and **Biedronka** (Biedronka flyers are recognised from images — prices may be inaccurate) (Kaufland flyers come from sklep.kaufland.pl through the same service as Lidl; where no price was found next to a product in the flyer, it's calculated from the per-kg/l price — “≈”). “Update flyers”; a **watchlist** (each shop has its own) with deal cards — price, was, −%, per kg, conditions (Kaufland card, “1+1”, limit), a link to the flyer page; next to it — how many times you bought the product at this shop and whether the deal beats your usual price **per kg/l** (packs of different sizes are compared fairly); **search** in the shop's flyers; 🔔 — a reminder to Telegram Saved Messages the day before the deal (automatic for the watchlist, only if the flyer has a discount or the price is 5%+ below the previous flyer for this product or your usual receipt price, and the watchlist words are in the product name: no reminders about regular prices); **inflation**: your personal one from the shop's receipts — a switch between **regular price** (without deals: the highest shelf price over the first and last ~3 months of purchases — at Kaufland the deal price can be right on the receipt) / **best price** (the lowest you paid with discounts); a spending-weighted average, without seasonal fruit and vegetables and without changes where the lower price looks like a deal (a one-off purchase or a short dip — marked); products you haven't bought for over half a year aren't counted; flavours of one product with different codes aren't mixed; a click on a product — its history, and the flyers for watchlist items; **price history** of a product — from receipts and from flyers, with a chart; you can also search by the name on the receipt (“K.GoudaPlastry500g”). The **“Where it's cheaper”** tab: the same thing right now in different shops (a column per enabled shop) — meat by kind (raw, cheapest per kg), the same product of one brand and your watchlist; compared per kg/l or per identical pack |
| **Analytics** | **month forecast**: spent since the 1st and usual by this day, forecast for the month end and a usual month, by category group (forecast = spent + recurring payments still expected + usual spending of the remaining days, the median over 6 months), income this month; a **calendar** of expected payments and income with the account balance after each; **recurring payments and income** — found in the statement and online orders (party, amount, interval), with confidence, next date, “more expensive”, “overdue”; “✓ recurring” / “✕ no” buttons |
| **Cash** | **total: cash + PKO account**; cash on hand, entries (count / income / spending without a receipt), movements and a chart |
| **Uncategorized** | on top — **unconfirmed payments**: a payment registration at Przelewy24/PayU that isn't in the statement (the provider cancels an unpaid registration by itself), or the same payment repeated a minute later; they aren't counted, buttons “paid” (paid with another card) and delete. Two different payments in a row (two courses for the same amount) aren't treated as a repeat. Below — items without a category — categorize and remember. **The name is clickable**: all occurrences open under the row — date and time, shop, source and payment, a link to the photo/email, the bank transaction (type, description, charge date), and for spending without a receipt — receipts and orders for that day and the two days before (a hint about what that BLIK was). The category can be set for one occurrence (“only this one”) |
| **Comments** | every item has a 💬 button — “what you bought” (e.g. for a BLIK without a name: “coffee”, “taxi”). The comment shows in lists, search finds it, and **the category is set from it** if you didn't set one manually: first the dictionary of the interface language, then the Polish product dictionary, then the other language (comments in English and Russian both work) |
| **Categories** | a tree with totals; add, rename, move, delete; the list of rules (by product code, name, shop / payee) |
| **Settings** | each source has an “in Update all” switch (a switched-off one updates only with the “update” button); sections collapse (what's open is remembered; the log is collapsed by default, its header shows a short status); **shops** — which ones are counted: a switched-off shop isn't updated and doesn't appear in Deals; the “Update all now” button and the update progress; sources: connected or not, when last updated successfully, data up to which date, what broke and which command to run (a click copies it); “update” for a single source; autostart (time, on/off); **language**; **deleted purchases** with a “restore” button; the update log with every step's output. A “!” on the tab — the last update failed, there were no updates yet, or bank access ends soon |

**Categories in the interface:**
- A category button shows the full path (“Health & beauty › Pharmacy”); a click opens a list with search
  (type “pha”, Enter).
- Changes aren't applied at once: a bar appears at the bottom “Unsaved category changes: N — **Save / Cancel**”.
- In the items table a change applies to all such items and is remembered as a rule.
  Inside a receipt an item has an “only this one” checkbox.
- For a **bank transaction without a receipt** “for all such” means **the whole shop or payee** (“all purchases at ALDI”,
  “all transfers to this person”), not the exact name with a BLIK number. Inside receipts the product dictionary beats the
  shop rule: a screwdriver from Lidl stays in “Repairs”. For “BLIK without a name” no rule is created — only this transaction.
- Purchases from photos and email have “📷 photo” / “✉ email” links to the original (the email is shown as text,
  without images, so trackers don't load).

---

## How it works

### Categories
A “category › subcategory” tree: 12 spending groups (Food, Alcohol & tobacco, Home & household, Health & beauty,
Goods & electronics, Transport, Housing, Phone & subscriptions, Leisure, Education, Finance, Other) + “Income” and “Transfers”
(not spending: Family, To people, From people, Debts & repayments, Cash withdrawal, Between own accounts).
The tree is defined in `core/categories.py` (`TREE`); every system category has a **permanent key** (`food.meat`,
`transfer.atm`…). The code and dictionaries refer to categories by key, so renaming and moving in the interface
break nothing. In the English interface the default categories are shown in English; categories you created keep your names.

An item's category is assigned by priority:
1. your manual “only this one” edit;
2. your comment on the item (“coffee”, “taxi”, “painkiller” → the comment dictionaries in `core/categories.py`,
   English and Russian);
3. a rule by product code / exact name (created when you change a category “for all such”);
4. **a bank transaction without a receipt:** a refund — the category of the original purchase; your shop / payee rule;
   otherwise — a statement rule (rent, tuition, taxes, fees, shops without a receipt…);
5. **a receipt or order item:** a shop whose item names say nothing (Jakdojade, Koleo → transport;
   Erecept → doctors); the keyword dictionary (merged Kaufland names and shortened Biedronka names are split into words first,
   Biedronka abbreviations like “Cz Milka”, “Mi mie”, “Zel Udroz” are known); the shop's product group; the shop department
   (Kaufland: “Słodycze”, “Mrożonki”…); your shop rule; the default category for the shop (pharmacies, Media Expert…).

### Your own rules (`config.ini`)
What applies only to you — employer, university, your places, small towns in the statement — is set
in `config.ini`, not in the code (sections `[bank]`, `[bank_rules]`, `[income_rules]`, `[merchants]`, `[shops]`,
`[shops_hint]`, `[mail_domains]`, `[mail_payees]`; format and examples in `config.ini.example`). So a copy of
the project for someone else doesn't contain your data.

### Bank reconciliation (`reconcile`)
1. Every purchase looks for a transaction in the statement: same amount, date −3…+5 days, a matching shop has priority.
   If a Tpay email contains a BLIK transaction number (“TR-…/88000000001”), the purchase is matched by it exactly —
   the bank writes the same number in the description.
   Purchases in euro — 6% tolerance (exchange rate and bank fee), the amount is taken from the bank.
2. Several orders of one shop paid with one transaction (a pharmacy) are joined.
3. Same shop, payment up to 14 days after the order, amount differs by up to 35% — a “soft” match,
   items are recalculated to the actual payment.
4. Transactions without a receipt become “bank” purchases; the category comes from the payee/description (rent, tuition, taxes,
   fees, Biedronka, Żabka…). Transfers to people and cash withdrawals — the “Transfers” categories.
5. Refunds to the card are linked to the original purchase (same amount or a product of the same amount within 90 days, its own bank
   transaction too): with a minus in its category and under the same shop, shown inside the purchase.
6. **Unconfirmed** — a payment registration without confirmation (Przelewy24/PayU) that isn't in the statement although the statement
   already covers that day, or the same amount for the same description repeated a minute later: not counted until you press “paid”.
   The “Ekspres Przelewy24 — przekazano do realizacji” email isn't a confirmation: someone else might have paid.
7. Cards of another account are detected by themselves (no match at all with 5+ purchases); the label is in `config.ini` `[cards]`.

BLIK transactions and card payments where the bank sends a number instead of a shop are grouped into the “shops”
“BLIK without a name” and “Card without a name” — comments are the easy way to identify them.
Purchases you deleted (the `hidden_purchases` table) are skipped on rebuilds; restore — Settings → Deleted purchases.

`python budget.py reconcile` — rebuild purchases from the originals and reconcile. Reconciliation itself is repeatable: amounts
adjusted to the bank (soft match, currency) are kept together with the receipt amounts (`orig_*`) and restored at the start
of every reconciliation, so it can run any number of times (after deleting a purchase the interface re-reconciles
without a rebuild). Manual category edits are kept. Receipt photos are re-parsed from the saved OCR text when the parser
got better (only if the result improves: “calculated from the total” or “check” → “total matches”).

### Cash
- Your entries: **count** (cash on hand now), **cash income**, **spending without a receipt**.
- Automatically: ATM withdrawal (+), deposit to the account (−), a receipt paid in cash (−).
- On a count the difference “calculated − counted” is spending without a receipt (category “Other › Cash without receipt”)
  or unrecorded income.
- The bank only gives the **date** of a deposit/withdrawal. The time is the interval between two statement downloads in which the transaction appeared; if your count falls inside that interval, the transaction is marked “set the time” — the 🕓 button in “Cash movements”.
- Events before your first wallet entry aren't counted: the first count is the starting point, not “unrecorded income”.
- **A cash deposit to the account is income** (“Income › Cash” for the statement transaction): so the Bank tab shows “received / spent”. In the wallet a deposit reduces the cash on hand. Wallet entries “cash received” and a plus on a count aren't income — they only change the balance, so the same money isn't counted twice.

---

## Backups

```
python budget.py backup password     # once: the backup password (into Windows Credential Manager)
python budget.py backup              # create a backup
python budget.py backup list
python budget.py backup restore budget_2026-10-01_172002.zip
```
- A backup is a ZIP with AES-256 encryption of the whole `budget` folder: the database (a consistent snapshot, fine with the interface open),
  emails, photos, receipt and statement originals, tokens, configs **and the code**. Only `data/tessdata`
  (downloaded again) and temporary files are left out. ~60 MB, ~10 seconds, verified after creation.
- Opens in 7-Zip / WinRAR with the password. **Write the password down separately** — without it the backup can't be opened.
- The last `keep` backups are kept (10 by default). A second folder (`copy_to` in `[backup]`) — a USB stick or the cloud:
  backups are encrypted, so the cloud is fine.
- `restore` unpacks into a separate folder `budget_restore_…` next to `budget` — the current data isn't touched.
  To roll back: close the interface, make a `backup` of the current state, replace `budget\data` with the unpacked one.

---

## Security

Everything is stored locally. The interface server listens only on `127.0.0.1`. Mail and bank are read-only.

**Files with access to your accounts — never send them to anyone:**

| File / place | What it is |
|---|---|
| `config.ini` | Telegram API keys |
| `data/lidl/token.json` | access to Lidl Plus |
| `data/kaufland/token.json` | access to Kaufland Card |
| `data/biedronka/session.json` | Moja Biedronka session cookies |
| `data/biedronka/downloads/` | downloaded receipt PDFs |
| `data/telegram.session` | sign-in to your Telegram |
| `data/bank/*.pem`, `data/bank/session.json` | the Enable Banking application key and statement access |
| Windows Credential Manager: `budget-gmail-imap`, `budget-backup` | Gmail app password, backup password |

Backups contain everything listed — that's why they're encrypted.
The Biedronka extension has access to the Biedronka domain cookies and sends them only to the local budget server
(`127.0.0.1:8765`); install the unpacked extension only from this project's folder.
Revoking access: Lidl/Kaufland — change the password; Gmail — delete the app password in the Google account;
bank — Unlink / Remove in the Enable Banking Control Panel or revoke the consent in iPKO.

---

## All commands

```
python budget.py serve                                   web interface
python budget.py update    [steps | status]              update everything / selected, log
python budget.py schedule  on [HH:MM] | off | status     daily update autostart
python budget.py lidl      login | sync | reparse        Lidl Plus
python budget.py kaufland  login | sync | reparse        Kaufland Card
python budget.py biedronka sync [--from D --to D]        Biedronka receipts (PDF from the site)
python budget.py telegram                                receipt photos from Telegram
python budget.py photos [folder|files]                   receipt photos/PDFs from disk (receipts/inbox by default)
python budget.py mail      login | headers | senders [N] | mark <label> <domains> | bodies | parse
python budget.py bank      login | sync | status | reparse
python budget.py bank import [files]                     statement from a file: CSV, MT940, camt.053 (without files — bank/inbox/)
python budget.py reconcile                               bank reconciliation (after bank sync — automatic)
python budget.py categorize                              recalculate categories
python budget.py unknown                                 uncategorized items (in the console)
python budget.py setcat "<pattern>" "Еда/Сладкое"        a category rule from the console
python budget.py item <product>                          product prices from receipts (in the console)
python budget.py backup    [password | list | restore <file>]
python budget.py upgrade   [--check]                     a new app version from GitHub (data isn't touched)
python -m pytest                                         tests
python tools/catsnap.py save [name] | diff [name]        a category snapshot and a “before → after” comparison
python tools/ocrbench.py [-v] [--fresh]                  photo recognition accuracy on receipts that also exist electronically
```
Terminal output of the commands is in Russian. Before editing the dictionary or rules: `catsnap save`, after — `catsnap diff`:
you see which items moved where and how the category totals changed. Snapshots are in `data/snapshots/`.
`reparse` — rebuild the database from already downloaded originals (after a parser fix), without downloading again.

---

## Troubleshooting

| Symptom | What to do |
|---|---|
| `Lidl/Kaufland не выдал токен`, `401` | the sign-in is outdated: “refresh token” in Settings (or `lidl login` / `kaufland login`) |
| The bank consent expired | “refresh token” in the “PKO statement” row (or `bank login`) |
| No connection to Enable Banking | the internet blinked; what's downloaded is saved — run `bank sync` again |
| Gmail won't let you in | you need an **app password**, not your normal password; repeat `mail login` |
| A receipt photo marked “⚠ check” | open “📷 photo”, fix the category/items; next time send the photo as a file |
| A purchase says “check: on … the bank has a transaction … without a receipt” | one expense may be counted twice (an order + a transaction) — check it manually |
| A payment is “unconfirmed” but it went through | you paid with another bank's card — “paid” on the Uncategorized page |
| Many “Uncategorized” after a new shop | categorize them in the interface — the rules are remembered |
| Port 8765 is busy | close the previous `serve` window (or the python process) |
| The database broke / need to roll back | `backup restore <file>` → replace `data` |
| A “!” on the Settings tab | the same page shows which step failed and which command to run; after it — “update” in the row |
| A step marked “error in the app” | expand the run in the log → “output” and send the text to Claude |
| A run “interrupted: the update process ended…” | the computer was turned off or the window closed mid-update; just run it again |
| Autostart doesn't run | Settings → Autostart: the state and the last run code; turn it off and on again. The task is `BudgetUpdate` in Task Scheduler |
| Part of the interface is still in Russian | that text isn't in the dictionary yet: in the browser console `untranslated()` lists it — add it to `app/static/js/i18n-en.js` |

---

## Project structure

```
budget/
  budget.py              entry point (all commands)
  config.ini             settings and keys (not in copies for others!)
  app/                   web interface: server.py (Flask) + static/ (index.html, style.css, js/*.js)
    static/js/i18n.js    interface language: translates the page by the dictionary i18n-en.js
  core/
    db.py                database schema (all of it), migrations (PRAGMA user_version), writing purchases
    categories.py        tree, dictionaries, category assignment, renaming/moving
    reconcile.py         bank reconciliation
    wallet.py            cash
    analytics.py         analytics: recurring payments, calendar, month forecast
    kaufland_deals.py    Kaufland flyers (PDF parsing, search, history)
    biedronka_deals.py   Biedronka flyers (page image recognition)
    prices.py            prices from receipts: product history, personal inflation
    backup.py            backups
    upgrade.py           a new app version from GitHub (“Check for updates” in Settings)
    update.py            “update all”, log, autostart (Windows Task Scheduler)
    deals.py             Lidl deals for the interface and “update all” (uses lidl-deals/lidl.py)
    i18n.py              the same dictionary for what the server sends itself (Telegram reminders)
    browser_login.py     sign-in through a Chrome window (OAuth/PKCE)
    logins.py            sign-in dates and average token lifetime (Settings page)
    stores.py            which shops are counted
    common.py            shared functions
  receipts/
    lidl.py, kaufland.py           receipts from the apps
    biedronka.py                   receipt PDFs through the web interface
    photos.py, textparse.py        receipt photos and receipt text parsing
    telegram_inbox.py              photos from Telegram
    mail.py, mail_parse.py, mail_orders.py   mail
  bank/enablebanking.py  PKO statement
  bank/statement.py      statement from a file: CSV, MT940, camt.053
  tests/                 tests (python -m pytest)
  tools/catsnap.py       category snapshot “before → after”
  tools/ocrbench.py      receipt photo recognition accuracy (text cache — data/ocr_cache/)
  tools/browser-connector/   the Chrome extension for the Biedronka session
  lidl-deals/            Lidl flyers and reminders (a separate module)
  data/
    budget.db            the database (purchases, items, bank transactions, categories, rules, cash)
    lidl/ kaufland/ biedronka/ bank/ mail/ photos/   originals and tokens
  backups/               backups (by default)
```

**Main `budget.db` tables:** `purchases` (a purchase: source, date, shop, total, discount, payment, link to
the bank transaction, note), `items` (items and their categories), `payments`, `bank_tx` (statement transactions),
`categories`, `rules`, `wallet_entries` (cash entries), `emails` and `mail_senders` (mail),
`update_runs` and `update_steps` (update log), `hidden_purchases` (deleted purchases), `item_notes` (item comments),
`confirmed_purchases` (“unconfirmed” payments you confirmed), `attachments` (photos attached to electronic receipts),
`wallet_times` (corrected cash transaction times), `recurring_marks` (recurring payments you confirmed or rejected), `meta`. A purchase has `refund_of` — the original purchase of a refund,
`status = 'doubt'` — unconfirmed, `orig_total` — the receipt amount before adjusting to the bank.

---

Version — see [CHANGELOG.md](CHANGELOG.md) (in Russian) and `python budget.py version`. License — [MIT](LICENSE): free to use and change, no warranty.
The unofficial Lidl Plus and Kaufland Card APIs may change at any time — then `receipts/lidl.py` / `receipts/kaufland.py` will need updating.

Code created using AI (Claude, Anthropic).
