# Generated by Django 4.2.11 on 2025-06-30 22:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0029_update_devotional_set_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='timezone',
            field=models.CharField(default='UTC', help_text="User's timezone in IANA format (e.g., 'America/New_York')", max_length=100),
        ),
        migrations.AlterField(
            model_name='llmprompt',
            name='active',
            field=models.BooleanField(default=False, help_text='If True, this prompt is the one currently used for generation'),
        ),
        migrations.AlterField(
            model_name='llmprompt',
            name='model',
            field=models.CharField(choices=[('gpt-4.1-mini', 'GPT 4.1 Mini'), ('gpt-4.1-nano', 'GPT 4.1 Nano'), ('gpt-4.1', 'GPT 4.1'), ('gpt-o4-mini', 'GPT o4 Mini (Reasoning $$$)'), ('gpt-4o-mini', 'GPT 4o Mini'), ('claude-3-7-sonnet-20250219', 'Claude 3.7 Sonnet'), ('claude-3-5-sonnet-20241022', 'Claude 3.5 Sonnet')], help_text='The LLM model used for generation', max_length=32),
        ),
    ]
