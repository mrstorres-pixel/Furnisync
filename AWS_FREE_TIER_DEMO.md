# AWS Free-Tier Demo Plan

This project can be presented on AWS using the smallest practical setup for a final defense.

## Recommended Demo Architecture

- `1 EC2 instance` for the Django application
- `Ubuntu` server image
- `Django + Gunicorn + Nginx`
- `PostgreSQL on the same EC2 instance` for a realistic demo
- `S3 optional` for media files if you want to show AWS storage usage

For a short-lived defense deployment, this is simpler and safer than adding RDS, Elastic Beanstalk, or a load balancer.

## Why This Is the Best Fit

- Lowest operational complexity
- Easy to explain during presentation
- Minimal AWS services means lower risk of surprise charges
- Good enough for one-branch demonstration traffic
- Still looks like a real cloud deployment

## AWS Services To Avoid For This Defense

- `RDS` unless you specifically need a managed database for the presentation
- `Elastic Beanstalk` because it adds hidden infrastructure complexity
- `Load Balancer` because a single-instance demo does not need it
- Extra EC2 instances or Auto Scaling

## Deployment Checklist

1. Create an EC2 instance that fits your account's free-tier or free-plan eligibility.
2. Open only the ports you need:
   - `22` for SSH
   - `80` for HTTP
   - `443` for HTTPS if you add SSL
3. Install system packages:
   - `python3`
   - `python3-venv`
   - `nginx`
   - `postgresql`
   - `libpq-dev`
   - `build-essential`
4. Clone or upload this project to the server.
5. Create a virtual environment and install dependencies from `requirements.txt`.
6. Create a PostgreSQL database and user for the app.
7. Set environment variables for:
   - `DJANGO_SECRET_KEY`
   - `POSTGRES_DB`
   - `POSTGRES_USER`
   - `POSTGRES_PASSWORD`
   - `POSTGRES_HOST`
   - `POSTGRES_PORT`
8. Run:
   - `python manage.py migrate`
   - `python manage.py createsuperuser`
   - `python manage.py collectstatic`
9. Start Django with Gunicorn behind Nginx.
10. Test login, dashboards, order screens, inventory screens, and payment logging.

## Billing Safety Rules

- Launch only one EC2 instance.
- Stop or terminate the instance after the defense if you no longer need it.
- Do not create extra databases, load balancers, or snapshots unless required.
- Check AWS Billing and Free Tier pages before and after deployment.

## Presentation Notes

For the defense, emphasize:

- role-based access control
- payment immutability
- receipt capture
- inventory monitoring
- reconciliation support
- audit logging
- cloud deployment readiness

## Suggested Next Steps In This Repo

1. Add production settings with `DEBUG=False`.
2. Add `ALLOWED_HOSTS` from environment variables.
3. Add Gunicorn to `requirements.txt`.
4. Add a small deployment guide with exact EC2 commands.
5. Optionally add S3 for uploaded receipt images after the base deployment works.
