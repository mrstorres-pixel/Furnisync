from __future__ import annotations

import random
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Customer


OLD_DEMO_PHONE_RE = re.compile(r"^09\d{2}100\d{4}$")


class Command(BaseCommand):
    help = "Replace unrealistic seeded demo customer phone numbers with more varied mobile numbers."

    def handle(self, *args, **options):
        candidates = list(
            Customer.objects.filter(phone__regex=r"^09[0-9]{2}100[0-9]{4}$").order_by("id")
        )
        if not candidates:
            self.stdout.write(self.style.WARNING("No demo customer phone numbers matched the repair pattern."))
            return

        phone_prefixes = [
            "0917", "0918", "0919", "0920", "0921", "0922", "0925", "0926", "0927", "0928",
            "0929", "0930", "0935", "0936", "0937", "0938", "0939", "0945", "0946", "0947",
            "0948", "0949", "0950", "0951", "0955", "0956", "0961", "0963", "0965", "0966",
            "0967", "0975", "0977", "0979", "0981", "0985", "0989", "0994", "0995",
        ]
        phone_suffixes = random.Random(20260419).sample(range(1000000, 10000000), len(candidates))

        updated = 0
        with transaction.atomic():
            for idx, customer in enumerate(candidates):
                if not OLD_DEMO_PHONE_RE.match(customer.phone or ""):
                    continue
                customer.phone = f"{phone_prefixes[idx % len(phone_prefixes)]}{phone_suffixes[idx]:07d}"
                customer.save(update_fields=["phone"])
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} demo customer phone numbers."))
