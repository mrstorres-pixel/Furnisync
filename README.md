# Furniture System - How to Add Secretary and Customers

## Adding Secretary (User with Secretary Role)

1. **Login as Owner/Admin** (role='owner')
2. Go to http://127.0.0.1:8000/users/
3. Click **Edit** on existing user or create new via Django admin if needed
4. In user form (core/templates/core/user_form.html):
   - Set **Role** to "Secretary" 
   - Assign **Branch** (optional)
5. Save - user now has secretary role with branch scoping

**Secretary permissions** (via role_required decorators in core/views.py):
- Create customers (`/create_customer`)
- View orders/inventory for their branch
- Dashboard at core/dashboard_secretary.html

## Adding Customers

1. **Login as Secretary/Manager/Owner**
2. Dashboard shows **"Create Customer"** link → http://127.0.0.1:8000/create_customer/
3. Fill form (core/forms.py CustomerForm):
   - Full name, phone, email, address
   - **Branch** (required)
   - Installment plan description
4. Submit - customer created with audit log

**Customers appear in**:
- Order creation forms
- Dashboard metrics (total_customers)
- Order lists filtered by branch/role

## Quick Setup Demo Accounts (Django shell)

**Run:**
```bash
cd FURNITURESYSTEM
python manage.py shell
```

**Copy-paste this code:**

```python
from core.models import Branch, UserProfile, UserRole
from django.contrib.auth.models import User

# Create Main Branch
branch, _ = Branch.objects.get_or_create(name="Main Branch", defaults={'address': '123 Main St'})

# 1. OWNER (admin)
owner = User.objects.get_or_create(username='owner', defaults={'email': 'owner@furniture.com'})[0]
owner.set_password('password123')
owner.save()
UserProfile.objects.update_or_create(user=owner, defaults={'role': UserRole.OWNER, 'branch': branch})

# 2. SECRETARY
sec = User.objects.get_or_create(username='secretary', defaults={'email': 'sec@furniture.com'})[0]
sec.set_password('password123')
sec.save()
UserProfile.objects.update_or_create(user=sec, defaults={'role': UserRole.SECRETARY, 'branch': branch})

# 3. COLLECTOR  
collector = User.objects.get_or_create(username='collector', defaults={'email': 'col@furniture.com'})[0]
collector.set_password('password123')
collector.save()
UserProfile.objects.update_or_create(user=collector, defaults={'role': UserRole.COLLECTOR, 'branch': branch})

# 4. MANAGER (bonus)
manager = User.objects.get_or_create(username='manager', defaults={'email': 'mgr@furniture.com'})[0]
manager.set_password('password123')
manager.save()
UserProfile.objects.update_or_create(user=manager, defaults={'role': UserRole.MANAGER, 'branch': branch})

print("✅ Demo accounts created!")
print("Login as: owner/secretary/collector/manager | Password: password123")
print("Visit /users/ to manage roles/branches")
```

**Test:**
1. `python manage.py runserver`
2. Login: `secretary` / `password123` → Secretary dashboard
3. Login: `collector` / `password123` → Collector features  
4. Login: `owner` / `password123` → Full admin (/users/)

## Test Flow
1. `python manage.py runserver`
2. Login: any user with profile.role='owner' 
3. /users/ → Edit/Add Secretary
4. /create_customer/ → Add customer
5. Dashboard shows updated metrics

## Django Admin Interface - Complete Procedure

### 1. Create Superuser Account
```bash
cd FURNITURESYSTEM
python manage.py createsuperuser
```
```
Username: admin
Email: admin@furniture.com
Password: admin123 (or your choice)
Confirm password: admin123
```

### 2. Start Server
```bash
python manage.py runserver
```

### 3. Login to Admin
1. Open **http://127.0.0.1:8000/admin/**
2. Login: `admin` / `admin123`

### 4. What You See & Do
**Sections:**
- **Users** → Add/edit regular users (these get app roles via Owner /users/)
- **User profiles** → Assign roles/branches
- **Branches** → Add branches  
- **Customers** → Raw customer data
- **Products** → Add products
- **Orders/Payments** → All transactions
- **Audit logs** → Track all changes

**Example: Add new user via Admin**
1. Users → **Add user**
2. Username: `newsec`, password: `pass123`
3. Save → goes to User profile
4. **User profiles** → Add: role="Secretary", branch="Main Branch"

### 5. Quick Test
```
Login admin → /admin/ → Users → Verify demo accounts
```

**Pro Tip:** Use app's Owner role (/users/) for business users, Admin for raw DB access only.

**Note:** Django Admin ≠ App Owner role (/users/ for role-based management).

See TODO.md for original fix status.
