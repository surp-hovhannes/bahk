from django.core.management.base import BaseCommand
from django.conf import settings
from hub.utils import test_mailgun_api
import json


class Command(BaseCommand):
    help = 'Test Mailgun API integration via Django Anymail'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Email address to send test email to (overrides EMAIL_TEST_ADDRESS setting)',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed configuration information',
        )

    def handle(self, *args, **options):
        self.stdout.write('=' * 60)
        self.stdout.write('Testing Mailgun API Integration')
        self.stdout.write('=' * 60)

        # Show configuration if verbose mode
        if options['verbose']:
            self.stdout.write('\nCurrent Email Configuration:')
            self.stdout.write(f'  EMAIL_BACKEND: {settings.EMAIL_BACKEND}')
            self.stdout.write(f'  DEFAULT_FROM_EMAIL: {settings.DEFAULT_FROM_EMAIL}')
            
            anymail_config = getattr(settings, 'ANYMAIL', {})
            self.stdout.write(f'  ANYMAIL Configuration:')
            for key, value in anymail_config.items():
                if 'API_KEY' in key:
                    # Mask API key for security
                    masked_value = f'{value[:8]}{"*" * (len(value) - 8)}' if len(value) > 8 else '***'
                    self.stdout.write(f'    {key}: {masked_value}')
                else:
                    self.stdout.write(f'    {key}: {value}')

        # Override test email address if provided
        if options['email']:
            original_test_address = settings.EMAIL_TEST_ADDRESS
            settings.EMAIL_TEST_ADDRESS = options['email']
            self.stdout.write(f'\nUsing custom test email: {options["email"]}')
        else:
            self.stdout.write(f'\nUsing default test email: {settings.EMAIL_TEST_ADDRESS}')

        self.stdout.write('\nSending test email...')

        # Call the test function
        result = test_mailgun_api()

        # Restore original test address if it was overridden
        if options['email']:
            settings.EMAIL_TEST_ADDRESS = original_test_address

        # Display results
        if result['success']:
            self.stdout.write(
                self.style.SUCCESS(f'\n✅ {result["message"]}')
            )
            self.stdout.write(f'   Backend: {result["backend"]}')
            self.stdout.write(f'   From: {result["from_email"]}')
            self.stdout.write(f'   To: {result["to_email"]}')
            if result.get('result'):
                self.stdout.write(f'   Send Result: {result["result"]}')
        else:
            self.stdout.write(
                self.style.ERROR(f'\n❌ Test failed: {result["error"]}')
            )
            self.stdout.write(f'   Backend: {result["backend"]}')
            
            if options['verbose'] and result.get('anymail_config'):
                self.stdout.write('   Anymail Config:')
                for key, value in result['anymail_config'].items():
                    if 'API_KEY' in key:
                        masked_value = f'{value[:8]}{"*" * (len(value) - 8)}' if len(value) > 8 else '***'
                        self.stdout.write(f'     {key}: {masked_value}')
                    else:
                        self.stdout.write(f'     {key}: {value}')

        self.stdout.write('\n' + '=' * 60)

        # Provide helpful next steps
        if result['success']:
            self.stdout.write(
                self.style.SUCCESS('✅ Mailgun API integration is working correctly!')
            )
            self.stdout.write('\nNext steps:')
            self.stdout.write('  • Check your email inbox for the test message')
            self.stdout.write('  • Your promotional emails will now be sent via Mailgun API')
            self.stdout.write('  • All notification emails are using the API instead of SMTP')
        else:
            self.stdout.write(
                self.style.ERROR('❌ Mailgun API integration needs attention')
            )
            self.stdout.write('\nTroubleshooting:')
            self.stdout.write('  • Verify your MAILGUN_API_KEY environment variable')
            self.stdout.write('  • Verify your MAILGUN_DOMAIN environment variable')
            self.stdout.write('  • Check that your domain is verified in Mailgun')
            self.stdout.write('  • Ensure django-anymail is properly installed')
            self.stdout.write('  • Run with --verbose for more configuration details')

        self.stdout.write('')