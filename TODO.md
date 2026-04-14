# Fix Payment Edit Error in log_payment View

## Status: In Progress

### Step 1: Read log_payment.html template [PENDING]
- Understand form structure and order selection.

### Step 2: Update PaymentForm in core/forms.py ✅
- Add validation to block editing existing payments.

### Step 3: Update log_payment view in core/views.py [IN PROGRESS]
- Prevent saving existing instances, pre-populate branch/collector.

### Step 4: Update log_payment.html template if needed [PENDING]
- Filter orders to unpaid only, remove hidden pk.

### Step 5: Test fix [PENDING]
- Runserver and test POST with new/existing order payment.

### Step 6: Mark complete [PENDING]

