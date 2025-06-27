from django.core.management.base import BaseCommand
from django.utils import timezone
from notifications.models import PromoEmail
from notifications.tasks import send_promo_email_task


class Command(BaseCommand):
    help = 'Retry sending failed promotional emails'

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
                if options['dry_run']:
                    self.stdout.write(f"Would retry PromoEmail {promo.id}: {promo.title}")
                    self.stdout.write(f"Current status: {promo.status}")
                    self.stdout.write(f"Recipients: {promo.recipient_count()}")
                else:
                    self.stdout.write(f"Retrying PromoEmail {promo.id}: {promo.title}")
                    promo.status = PromoEmail.DRAFT
                    promo.save()
                    send_promo_email_task.delay(promo.id)
                    self.stdout.write(self.style.SUCCESS(f"Successfully queued retry for {promo.title}"))
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
                for promo in failed_emails:
                    promo.status = PromoEmail.DRAFT
                    promo.save()
                    send_promo_email_task.delay(promo.id)
                    count += 1
                    self.stdout.write(f"Queued retry for: {promo.title}")
                
                self.stdout.write(self.style.SUCCESS(f"Successfully queued {count} emails for retry")) 