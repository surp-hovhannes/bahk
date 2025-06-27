from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core.cache import cache
from notifications.models import PromoEmail
from django.conf import settings


class Command(BaseCommand):
    help = 'Check the status of promotional emails and rate limiting'

    def handle(self, *args, **options):
        # Check current rate limiting status
        current_count = cache.get('email_count', 0)
        self.stdout.write(f"\n📧 Email Rate Limiting Status:")
        self.stdout.write(f"  Current count: {current_count}/{settings.EMAIL_RATE_LIMIT}")
        self.stdout.write(f"  Window: {settings.EMAIL_RATE_LIMIT_WINDOW} seconds")
        
        if current_count >= settings.EMAIL_RATE_LIMIT:
            self.stdout.write(self.style.WARNING("  ⚠️  Rate limit reached!"))
        else:
            remaining = settings.EMAIL_RATE_LIMIT - current_count
            self.stdout.write(self.style.SUCCESS(f"  ✅ {remaining} emails remaining in current window"))

        # Check recent promotional emails
        today = timezone.now().date()
        yesterday = today - timezone.timedelta(days=1)
        
        recent_emails = PromoEmail.objects.filter(
            created_at__gte=yesterday
        ).order_by('-created_at')

        self.stdout.write(f"\n📊 Recent Promotional Emails (last 24h):")
        
        status_counts = {
            PromoEmail.DRAFT: 0,
            PromoEmail.SCHEDULED: 0,
            PromoEmail.SENDING: 0,
            PromoEmail.SENT: 0,
            PromoEmail.FAILED: 0,
            PromoEmail.CANCELED: 0,
        }
        
        for email in recent_emails:
            status_counts[email.status] += 1

        for status, count in status_counts.items():
            if count > 0:
                if status == PromoEmail.FAILED:
                    style = self.style.ERROR
                    icon = "❌"
                elif status == PromoEmail.SENDING:
                    style = self.style.WARNING
                    icon = "⏳"
                elif status == PromoEmail.SENT:
                    style = self.style.SUCCESS
                    icon = "✅"
                else:
                    style = self.style.HTTP_INFO
                    icon = "📝"
                    
                self.stdout.write(style(f"  {icon} {status.title()}: {count}"))

        # Show details of failed or sending emails
        problematic_emails = recent_emails.filter(
            status__in=[PromoEmail.FAILED, PromoEmail.SENDING]
        )
        
        if problematic_emails.exists():
            self.stdout.write(f"\n🔍 Emails needing attention:")
            for email in problematic_emails:
                status_icon = "❌" if email.status == PromoEmail.FAILED else "⏳"
                self.stdout.write(f"  {status_icon} ID {email.id}: {email.title}")
                self.stdout.write(f"     Status: {email.status}")
                self.stdout.write(f"     Recipients: {email.recipient_count()}")
                self.stdout.write(f"     Created: {email.created_at.strftime('%Y-%m-%d %H:%M')}")
                if email.sent_at:
                    self.stdout.write(f"     Sent: {email.sent_at.strftime('%Y-%m-%d %H:%M')}")
                self.stdout.write("")

        # Suggestions
        self.stdout.write(f"\n💡 Suggestions:")
        if status_counts[PromoEmail.FAILED] > 0:
            self.stdout.write("  • Run: python manage.py retry_failed_emails --dry-run")
            self.stdout.write("  • Check Mailgun logs for specific error details")
        if status_counts[PromoEmail.SENDING] > 0:
            self.stdout.write("  • Emails marked as 'sending' may be in queue due to rate limits")
            self.stdout.write("  • This is normal behavior - they will process automatically")
        if current_count >= settings.EMAIL_RATE_LIMIT:
            minutes_to_wait = settings.EMAIL_RATE_LIMIT_WINDOW // 60
            self.stdout.write(f"  • Wait {minutes_to_wait} minutes before sending more emails") 