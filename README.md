# Furnisync

Furnisync is a single-branch furniture store information management system built for an academic project and final defense. It is designed to support day-to-day branch operations with a focus on customers, products being sold, orders, collections, employee accountability, reconciliation, and auditability.

The system is intentionally scoped to one operating branch. Instead of trying to model a multi-branch enterprise, it focuses on the workflow of one furniture showroom handling customer records, product sales, stock control, collector activity, and management review.

## Current Scope

Furnisync currently supports:

- Customer record management
- Product catalog and branch stock tracking
- Order creation and order workflow control
- Collector-assigned payment logging
- Receipt generation per payment
- Customer payment confirmation flow with receipt upload and digital signature
- Fraud and review flags for suspicious collection activity
- Daily cash reconciliation
- Audit trail for sensitive actions
- Role-based access for owner, manager, secretary, and collector

## Business Roles

The system uses four business roles:

- `owner`
  Full oversight of users, audit trail, fraud review, transactions, employees, catalog, orders, and customers.

- `manager`
  Operational control over orders, stock, employees, transactions, fraud review, reconciliation review, and product-level stock management.

- `secretary`
  Encodes customers and orders, works with the catalog and stock in a read-oriented way, and supports front-office order handling.

- `collector`
  Focused only on collection work: assigned collectible orders, payment logging, receipts, and daily cash reporting.

## Main Functional Areas

### 1. Customer Management

The system maintains customer records with:

- full name
- phone number
- email
- address
- installment plan description
- branch association

Customer pages summarize order history, purchase totals, and outstanding balances.

### 2. Catalog and Stock

Products and inventory are now treated as one operational area called `Catalog & Stock`.

Each product can have:

- category
- name
- SKU
- description
- unit price
- branch stock values

Stock is tracked through:

- `stock`
- `reserved`
- `available`

Managers and owners manage stock from the product page itself. The product detail page now acts as the control center for:

- product information
- branch stock position
- stock adjustment
- recent stock movement
- recent completed sales

### 3. Orders

Orders are attached to customers and carry a workflow status:

- `pending`
- `reserved`
- `completed`
- `cancelled`

Orders include one or more line items, each with:

- product
- quantity
- locked selling price
- subtotal

Current order rules include:

- duplicate products are blocked within the same order
- unavailable stock prevents improper reservation/completion
- only assigned collectors should see collectible orders
- order changes can be routed through change requests for approval

### 4. Payments and Receipts

Collectors log payments against assigned orders. Each payment creates a receipt record and can include uploaded proof from the collector.

The receipt flow supports:

- collector receipt upload
- generated receipt number
- payment record tied to an order
- remaining balance tracking

### 5. Customer Confirmation and Anti-Fraud Controls

To improve accountability for collections, the system includes a customer confirmation flow. After a payment is logged, a customer confirmation link and QR-based confirmation flow can be used.

The confirmation process supports:

- customer name confirmation
- confirmed amount
- customer receipt upload
- digital signature

Fraud-related checks currently include:

- suspicious same-device or same-IP detection
- customer-vs-collector amount comparison
- manager review workflow for flagged payments

This allows the system to show when a payment should be:

- matched
- pending customer confirmation
- marked for review

### 6. Daily Reconciliation

Collectors can report daily cash totals, and management can compare:

- system total
- physically counted cash
- discrepancy

This supports branch-level cash control and helps surface shortages or mismatches.

### 7. Audit Trail

Furnisync logs important business actions for traceability. This includes changes such as:

- customer creation
- order creation and management changes
- inventory adjustments
- reconciliation decisions
- payment review decisions

The audit trail is intended for management and ownership review rather than day-to-day collector use.

## Workflow Summary

The intended branch workflow is:

1. Secretary creates or updates a customer record.
2. Secretary creates an order using products from the catalog.
3. Manager or owner oversees order progression when needed.
4. A collector is assigned to collectible orders.
5. Collector logs a payment and uploads a collector receipt.
6. A receipt is generated for the transaction.
7. Customer confirmation may be captured through the confirmation link or QR flow.
8. Suspicious cases are flagged for management review.
9. Collector submits daily cash reconciliation.
10. Manager or owner reviews reconciliation, fraud flags, and audit history.

## Tech Stack

Core technologies currently used in the project:

- Python 3.12
- Django 5
- PostgreSQL
- Gunicorn
- Nginx
- Pillow
- AWS EC2 for deployment

Python dependencies in `requirements.txt`:

- `Django>=5.1,<6.0`
- `psycopg2-binary>=2.9`
- `gunicorn>=23.0`
- `Pillow>=10.0`

## Local Development Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
```

### 2. Activate it

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

On Linux/macOS:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

Create your environment file based on `.env.example` and set the database, allowed hosts, and Django settings needed for your machine.

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Create a superuser if needed

```bash
python manage.py createsuperuser
```

### 7. Start the server

```bash
python manage.py runserver
```

## Demo Data Commands

The project includes management commands for demo/reset workflows.

### Reset business data only

This removes operational data while keeping accounts and roles available.

```bash
python manage.py reset_business_data
```

### Seed demo data

This populates the system with fabricated but realistic demo data for presentation.

```bash
python manage.py seed_demo_data
```

If you want to reset business data first and then reseed in one step:

```bash
python manage.py seed_demo_data --fresh
```

### Repair stock from order history

If stock needs to be recalculated from existing orders:

```bash
python manage.py repair_inventory_from_orders
```

## Deployment Notes

The project is currently intended for lightweight AWS EC2 deployment suitable for a final project demo using free-tier-friendly services where possible.

Typical production stack used in this project:

- Django app
- Gunicorn service
- Nginx reverse proxy
- PostgreSQL database
- Ubuntu EC2 instance

## Full Deployment Command Set

### Local machine: commit and push

Run from Windows PowerShell:

```powershell
cd "C:\Users\Admin\Desktop\FURNITURESYSTEM (2)\FURNITURESYSTEM\FURNITURESYSTEM"
git status
git add .
git commit -m "Describe your changes here"
git push origin main
```

### Connect to the server

```powershell
ssh -i "C:\Users\Admin\Downloads\furnisync-keypair.pem" ubuntu@18.140.248.97
```

### Server: pull and deploy

```bash
cd /home/ubuntu/FURNITURESYSTEM
git pull origin main
source .venv/bin/activate
set -a
source .env.server
set +a
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
sudo systemctl restart furnituresystem
sudo systemctl restart nginx
sudo systemctl status furnituresystem --no-pager
sudo systemctl status nginx --no-pager
```

## Important Presentation Notes

For presentation purposes, the current system is strongest when described as:

- a single-branch furniture operations system
- focused on customers, products sold, collections, and accountability
- designed with auditability and fraud detection in mind

It is not positioned as:

- a marketplace
- a customer self-service portal
- a multi-branch enterprise ERP

## Current UX Direction

Recent improvements in the current version include:

- cleaner professional UI
- simplified copy across screens
- floating left navigation on desktop
- unified `Catalog & Stock` tab instead of split product/inventory tabs
- product-centered stock adjustment workflow
- role-focused dashboards and permissions

## Known Boundaries

This project is intentionally scoped for academic demonstration. It is functional and presentation-ready, but still has natural next-step improvements if it were to become a production business system, such as:

- stronger HTTPS/SSL setup
- deeper approval chains
- richer reporting exports
- more advanced image/document storage strategy
- stronger external identity verification

## Project Identity

System name: `Furnisync`

Intended use: single-branch furniture store information management and control
