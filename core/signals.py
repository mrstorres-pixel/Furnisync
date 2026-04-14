from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Branch, Payment, Receipt, UserProfile, UserRole

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance: User, created: bool, **kwargs):
    """
    Ensure every user has an associated profile with a role.
    Default role is Collector with no branch. For superusers we default
    to Owner/Admin so they have full access to all dashboards.
    """
    if created:
        role = UserRole.OWNER if instance.is_superuser else UserRole.COLLECTOR
        default_branch = Branch.objects.order_by("id").first()
        UserProfile.objects.create(user=instance, role=role, branch=default_branch)


@receiver(post_save, sender=Payment)
def create_payment_receipt(sender, instance: Payment, created: bool, **kwargs):
    """
    Auto-generate a Receipt record for every new Payment.
    """
    if created:
        receipt_number = Receipt.generate_receipt_number(instance)
        remaining_balance = instance.balance_after_payment
        
        Receipt.objects.create(
            receipt_number=receipt_number,
            payment=instance,
            order=instance.order,
            branch=instance.branch,
            collector=instance.collector,
            total_paid=instance.amount,
            remaining_balance=remaining_balance,
        )

