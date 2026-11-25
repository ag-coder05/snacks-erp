# Order-to-Audit ERP for a Small South Indian Food Manufacturer

A full-stack Django + PostgreSQL prototype that connects a small packaged snacks factory's **Production Loop** (Purchases → BOM Production Run → Variance & Costing) and **Sales Loop** (Mobile Field Order → Admin Verification → GST Invoice & PDF → Dispatch → Payments) through a single, immutable **Stock Ledger**.

## Tech Stack & Justification
* **Django (Python) + PostgreSQL:** Chosen for rapid, reliable transactional modelling. Python's native `decimal.Decimal` prevents floating-point drift in money and inventory math, while Django's `transaction.atomic()` guarantees that production runs, dispatches, and cancellations update the stock ledger and source documents together.
* **Tailwind CSS (Server-Rendered Templates):** Keeps mobile field-order screens and desktop admin/auditor dashboards fast and lightweight without the overhead of maintaining a separate SPA frontend.
* **WeasyPrint & OpenPyXL:** Generates print-ready GST tax invoice PDFs and auditor-ready Excel/CSV exports directly from database queries.

## Quick Start

### Option 1: One-Command Run with Docker Compose (PostgreSQL)
```bash
docker compose up --build
```
This automatically starts PostgreSQL, runs migrations, seeds the Section 5 Murukku benchmark data, and serves the app at `http://127.0.0.1:8000`.

### Option 2: Local Python Setup (SQLite)
```bash
python -m venv venv
# Windows: .\venv\Scripts\Activate.ps1  |  Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_benchmark
python manage.py runserver
```

### Run Automated Benchmark Tests
```bash
python manage.py test
```
This executes all 9 steps of the Section 5 Murukku 200 g scenario and asserts every closing balance, variance flag, GST split, outstanding receivable, and cost per packet.

## Test Logins (Password: `pass1234` for all roles)
| Role | Username | Access Scope |
| :--- | :--- | :--- |
| **Salesman A / B** | `salesman_a`, `salesman_b` | Mobile order entry, own order status & history |
| **Factory Admin** | `admin_user` | Order approval, stock checks, GST invoicing/PDF, dispatch, payments |
| **Production Supervisor** | `prod_sup` | Purchases, BOM production runs, actuals/wastage, overheads |
| **Accountant / Auditor** | `auditor` | Read-only access to reports, stock ledger, and Excel/CSV exports |
| **Owner** | `owner` | Live dashboard, stock levels, variance alerts, receivables summary |

## Scope Coverage (Section 6 Checklist)
* **Must Have:** Role-based login (5 roles), Product/Customer/Supplier/BOM masters, mobile-friendly order entry, admin approval with live stock checks, GST invoice PDF (CGST/SGST & IGST), atomic BOM production runs, planned vs actual variance flags, ledger-backed stock summary & stock ledger, Excel/CSV exports.
* **Should Have:** Purchases & supplier register, payments & customer outstanding tracking, weighted-average cost per packet with labour/overhead allocation, GSTR-1/3B outward summary, variance tolerance alerts (±3%), Owner summary dashboard.
* **Built-in Safeguards:** Audit log on sensitive operations; posted invoices are never deleted—cancelled via Credit Note with automatic stock reversal.

## Section 5 Benchmark Verification Results
| Check | Expected | System Output |
| :--- | :--- | :--- |
| Flour closing balance | 120 kg | `120.000 kg` |
| Cooking oil closing balance | 19.5 L | `19.500 litre` |
| 200 g Pouches closing balance | 306 pcs | `306.000 piece` |
| Finished Murukku (after run) | 238 packets | `238.000 packet` |
| Finished Murukku (after dispatch) | 88 packets | `88.000 packet` |
| Yield variance | -6%, flagged | `-6.00%` (Flagged, >3% tolerance) |
| Oil consumption variance | +5%, flagged | `+5.00%` (Flagged, >3% tolerance) |
| Retailer A (TN) GST Invoice | ₹7,500 + ₹187.50 CGST + ₹187.50 SGST = ₹7,875 | `₹7,875.00` (Intra-state) |
| Retailer A outstanding (after ₹4,000) | ₹3,875 | `₹3,875.00` |
| Distributor B (KA) 120-pkt order | Shortage of 32 flagged; IGST applies | `Shortage of 32.000 packets flagged` + `Inter-State (IGST 5%)` |
| **Cost per packet** | Calculated with rule stated | **`₹31.14 / packet`** (Margin: `₹18.86` vs ₹50 SP) |

*Costing note:* Using **Weighted Average** valuation across opening stock and purchases, Flour averages `₹43.125/kg` (`(100×42 + 60×45)/160`). Total material cost (`₹4,055.00`, including 6 damaged pouches) + Labour (`₹1,200`) + Gas/Power (`₹600`) = `₹5,855.00 / 188 good packets = ₹31.14/packet`.

## Assumptions & Known Gaps
* **Assumptions:** Factory is located in Tamil Nadu; standard 5% GST applies to packaged snacks; damaged packaging pouches are charged to the production run's material cost.
* **Known Gaps:** Offline PWA order queueing, batch/expiry FEFO tracking, and Tally XML export are out of scope for this initial prototype.