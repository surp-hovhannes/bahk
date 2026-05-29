from django.core.management.base import BaseCommand
from django.utils import timezone
from notifications.models import PromoEmail
from notifications.tasks import send_promo_email_task


class Command(BaseCommand):
    help = 'Retry sending failed promotional emails'

    def claim_failed_promo(self, promo_id):
        updated = PromoEmail.objects.filter(
            id=promo_id,
            status=PromoEmail.FAILED,
        ).update(status=PromoEmail.DRAFT)
        if updated != 1:
            return None
        return PromoEmail.objects.get(id=promo_id)

    def add_arguments(self, parser):
        parser.add_argument(
            '--promo-id',
            type=int,
            help='Specific PromoEmail ID to retry',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be retried without actually doing it',
        )

    def handle(self, *args, **options):
        if options['promo_id']:
            # Retry specific email
            try:
                promo = PromoEmail.objects.get(id=options['promo_id'])
                if promo.status != PromoEmail.FAILED:
                    self.stdout.write(
                        self.style.ERROR(
                            f"PromoEmail {promo.id} has status {promo.status}; only failed emails can be retried"
                        )
                    )
                    return

                if options['dry_run']:
                    self.stdout.write(f"Would retry PromoEmail {promo.id}: {promo.title}")
                    self.stdout.write(f"Current status: {promo.status}")
                    self.stdout.write(f"Recipients: {promo.recipient_count()}")
                else:
                    claimed_promo = self.claim_failed_promo(promo.id)
                    if claimed_promo is None:
                        self.stdout.write(
                            self.style.ERROR(
                                f"PromoEmail {promo.id} was already claimed or is no longer failed"
                            )
                        )
                        return
                    self.stdout.write(f"Retrying PromoEmail {claimed_promo.id}: {claimed_promo.title}")
                    send_promo_email_task.delay(claimed_promo.id)
                    self.stdout.write(self.style.SUCCESS(f"Successfully queued retry for {claimed_promo.title}"))
            except PromoEmail.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"PromoEmail with ID {options['promo_id']} not found"))
        else:
            # Retry all failed emails from last 24 hours
            yesterday = timezone.now() - timezone.timedelta(days=1)
            failed_emails = PromoEmail.objects.filter(
                status=PromoEmail.FAILED,
                created_at__gte=yesterday
            )
            
            if options['dry_run']:
                self.stdout.write(f"Would retry {failed_emails.count()} failed emails:")
                for promo in failed_emails:
                    self.stdout.write(f"  - {promo.id}: {promo.title} ({promo.recipient_count()} recipients)")
            else:
                count = 0
                for promo_id in failed_emails.values_list('id', flat=True):
                    promo = self.claim_failed_promo(promo_id)
                    if promo is None:
                        continue
                    send_promo_email_task.delay(promo.id)
                    count += 1
                    self.stdout.write(f"Queued retry for: {promo.title}")
                
                self.stdout.write(self.style.SUCCESS(f"Successfully queued {count} emails for retry")) 
