Redacted Azure invoice deposited with the replication package
=============================================================

File            G182569155_redacted.pdf  (4 pages, image-only by design; no
                text layer)
SHA-256         3537749dbf5714122fb6c5c64fec86ebc4051e61a1ae38c9ea712ff154f6918f

Invoice number  G182569155
Invoice date    2026-09-09
Billing period  2026-08-16 to 2026-08-31
Subtotal (net)  GBP 151.70
VAT (20%)       GBP 30.34
Total (gross)   GBP 182.04
Azure credit    "Azure Credit 0" -- no promotional credit was applied
Payment         charged to a personal card

Reconciliation identity
  (168.2 + 25.79) AQT x GBP 0.78201 = GBP 151.702 = the invoice subtotal to
  the penny. 168.2 AQT and 25.79 AQT are the amountBilled values in the two
  platform billing payloads stored in data/raw/thesis_azure_ionq.json
  (phase 4, 1000 shots; phase 6, 50 shots). src/reconcile_ledger.py checks
  this identity on every run (--reconcile-invoice runs the check alone).

What was redacted
  billing-profile name; sold-to and bill-to name and address; section name;
  admin-portal URL.

What was retained
  invoice number and date; billing period; every line item with its meter,
  quantity, unit price and amount; the "Azure Credit 0" line; subtotal, VAT
  and total; currency and payment method.

Not deposited
  Invoices G178482442 and G185212392 are GBP 0.00 marketplace plan-enrolment
  invoices (provider plan activation) carrying no usage; they are not
  deposited.

Consequence for the ledger
  ledger/ledger.csv contains two azure_ionq credits_covered_adjustment rows
  (2026-08-17T13:18:00, -168.36; 2026-08-17T14:05:00, -25.81) recording an
  expectation that Azure promotional credit would absorb these bills. The
  invoice shows it did not. The rows are retained verbatim as part of the
  primary record; ledger/ledger_reconciliation.{csv,json} derive the cash
  figures from the invoice instead (see CHANGELOG.md v1.2).
