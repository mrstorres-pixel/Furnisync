# EC2 Demo Deployment

This guide is for a short-lived final defense deployment on a single AWS EC2 instance.

## Stack

- Ubuntu EC2 instance
- Django app
- Gunicorn
- Nginx
- PostgreSQL on the same server

## 1. Install System Packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx postgresql postgresql-contrib libpq-dev build-essential
```

## 2. Create PostgreSQL Database

```bash
sudo -u postgres psql
```

Inside PostgreSQL:

```sql
CREATE DATABASE furnituresystem;
CREATE USER furnituresystem_user WITH PASSWORD 'change-me';
ALTER ROLE furnituresystem_user SET client_encoding TO 'utf8';
ALTER ROLE furnituresystem_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE furnituresystem_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE furnituresystem TO furnituresystem_user;
\q
```

## 3. Upload The Project

Place the project on the server, then move into the project folder:

```bash
cd FURNITURESYSTEM
```

## 4. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Configure Environment Variables

Copy `.env.example` and adjust values for the EC2 host:

```bash
cp .env.example .env
cp deploy/.env.server.example .env.server
```

Export the variables before running Django, or load them from your shell profile:

```bash
export DJANGO_SECRET_KEY='replace-this'
export DJANGO_DEBUG='False'
export DJANGO_ALLOWED_HOSTS='your-ec2-public-dns,your-ec2-public-ip'
export DJANGO_CSRF_TRUSTED_ORIGINS='http://your-ec2-public-dns'
export POSTGRES_DB='furnituresystem'
export POSTGRES_USER='furnituresystem_user'
export POSTGRES_PASSWORD='change-me'
export POSTGRES_HOST='127.0.0.1'
export POSTGRES_PORT='5432'
```

For the systemd service later, edit `.env.server` with the same values.

## 6. Prepare The Database And Static Files

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py check
```

## 7. Test Gunicorn Directly

```bash
gunicorn furnituresystem.wsgi:application -c gunicorn.conf.py
```

Open `http://your-ec2-public-ip:8000` to confirm the app works.

## 8. Configure Gunicorn As A Service

Copy the example service file into systemd:

```bash
sudo cp deploy/gunicorn.service.example /etc/systemd/system/furnituresystem.service
```

Then reload and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable furnituresystem
sudo systemctl start furnituresystem
sudo systemctl status furnituresystem
```

## 9. Configure Nginx

Create `/etc/nginx/sites-available/furnituresystem`:

```nginx
server {
    listen 80;
    server_name your-ec2-public-ip your-ec2-public-dns;

    location /static/ {
        alias /home/ubuntu/FURNITURESYSTEM/staticfiles/;
    }

    location /media/ {
        alias /home/ubuntu/FURNITURESYSTEM/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Or copy the included example file and edit `server_name`:

```bash
sudo cp deploy/nginx.furnituresystem.conf.example /etc/nginx/sites-available/furnituresystem
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/furnituresystem /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## 10. Recommended Demo Flow

Before the defense:

1. Confirm the Gunicorn service is active.
2. Open the login page and verify the dashboards load.
3. Test one payment flow with receipt upload.
4. Confirm static files and media load correctly.

## 11. After The Defense

To avoid unnecessary charges:

1. Stop the EC2 instance if you still need it later.
2. Terminate the EC2 instance if the project is finished.
3. Check AWS Billing to confirm no extra resources were left running.
