from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def mask_customer_name(value: str | None) -> str:
    if not value:
        return ""
    parts = str(value).split()
    if not parts:
        return ""
    first_name = parts[0]
    last_name = " ".join(parts[1:])
    masked_first = first_name[:2]
    if len(first_name) > 2:
        masked_first += "*" * (len(first_name) - 2)
    return f"{masked_first} {last_name}".strip()


@register.filter
def mask_email(value: str | None) -> str:
    if not value:
        return ""
    email = str(value)
    if "@" not in email:
        visible = email[:3]
        hidden = "*" * max(len(email) - 3, 0)
        return f"{visible}{hidden}"
    local_part, domain = email.split("@", 1)
    visible = local_part[:3]
    hidden = "*" * max(len(local_part) - 3, 0)
    return f"{visible}{hidden}@{domain}"


@register.filter
def mask_phone(value: str | None) -> str:
    if not value:
        return ""
    phone = str(value)
    visible = phone[:5]
    hidden = "*" * max(len(phone) - 5, 0)
    return f"{visible}{hidden}"
