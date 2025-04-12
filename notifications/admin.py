from django.contrib import admin
from django.contrib import messages
from django.template.response import TemplateResponse
from django.urls import path
from django.shortcuts import redirect
from .models import DeviceToken
from .utils import send_push_notification
from .tasks import send_push_notification_task, send_promo_email_task
from django.contrib.admin import SimpleListFilter
from hub.models import Fast
from django.utils import timezone
from django.db.models import Q
from django import forms
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.loader import render_to_string
from .models import PromoEmail
from django.contrib.admin.views.decorators import staff_member_required
from django.conf import settings


import json
import logging

logger = logging.getLogger(__name__)


class UserWithNoFastsFilter(SimpleListFilter):
    title = 'User Fast Status'  # Display name in admin
    parameter_name = 'user_fast_status'  # URL parameter

    def lookups(self, request, model_admin):
        return (
            ('no_fasts', 'Users with no fasts'),
            ('has_fasts', 'Users with fasts'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'no_fasts':
            return queryset.filter(user__profile__fasts__isnull=True).distinct()
        if self.value() == 'has_fasts':
            return queryset.filter(user__profile__fasts__isnull=False).distinct()
        return queryset


class UserFastFilter(SimpleListFilter):
    title = 'Joined Fast'
    parameter_name = 'joined_fast'

    def lookups(self, request, model_admin):
        # Get fasts that have future days
        today = timezone.now().date()
        fasts = Fast.objects.filter(
            days__date__gte=today
        ).distinct().order_by('name')
        
        return [(fast.id, f"{fast.name} ({fast.year})") for fast in fasts]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(user__profile__fasts__id=self.value()).distinct()
        return queryset


class UserNotJoinedFastFilter(SimpleListFilter):
    title = 'Not Joined Fast'
    parameter_name = 'not_joined_fast'

    def lookups(self, request, model_admin):
        # Get fasts that have future days
        today = timezone.now().date()
        fasts = Fast.objects.filter(
            days__date__gte=today
        ).distinct().order_by('name')
        
        return [(fast.id, f"{fast.name} ({fast.year})") for fast in fasts]

    def queryset(self, request, queryset):
        if self.value():
            # Get users who have joined the fast
            users_joined = queryset.filter(user__profile__fasts__id=self.value()).values_list('user_id', flat=True)
            # Return users who haven't joined the fast
            return queryset.exclude(user_id__in=users_joined).distinct()
        return queryset


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ('token', 'user', 'device_type', 'is_active', 'created_at', 'last_used')
    list_filter = ('device_type', 'is_active')
    search_fields = ('token', 'user__email')
    ordering = ('-created_at',)
    actions = ['send_push_notification', 'send_test_notification']
    list_filter = (UserWithNoFastsFilter, UserFastFilter, UserNotJoinedFastFilter, 'is_active', 'created_at', 'last_used')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'send-push/',
                self.admin_site.admin_view(self.send_push_view),
                name='notifications_devicetoken_send_push',
            ),
        ]
        return custom_urls + urls
    
    def send_push_notification(self, request, queryset):
        """Redirect to push notification form"""
        selected = queryset.values_list('id', flat=True)
        request.session['selected_tokens'] = list(selected)
        return self.send_push_view(request)
    send_push_notification.short_description = "Send push notification to selected tokens"

    def send_push_view(self, request):
        """Custom view for sending push notifications"""
        context = {
            **self.admin_site.each_context(request),
            'title': 'Send Push Notification',
            'opts': self.model._meta,
            'has_permission': True,
        }

        if request.method == 'POST':
            message = request.POST.get('message')
            data_str = request.POST.get('data', '{}')
            selected_ids = request.session.get('selected_tokens', [])
            
            if not message:
                messages.error(request, 'Message is required')
                return TemplateResponse(
                    request,
                    'admin/notifications/devicetoken/send_push.html',
                    context
                )

            try:
                data = json.loads(data_str) if data_str else None
            except json.JSONDecodeError:
                messages.error(request, 'Invalid JSON data format')
                return TemplateResponse(
                    request,
                    'admin/notifications/devicetoken/send_push.html',
                    context
                )

            try:
                # Get the users associated with the selected tokens
                tokens = DeviceToken.objects.filter(
                    id__in=selected_ids,
                    is_active=True
                )
                
                if not tokens:
                    messages.error(request, 'No active tokens selected')
                    return TemplateResponse(
                        request,
                        'admin/notifications/devicetoken/send_push.html',
                        context
                    )

                users = list(tokens.values_list('user', flat=True).distinct())
                
                result = send_push_notification(
                    message=message,
                    data=data,
                    users=users
                )

                if result['success']:
                    messages.success(
                        request,
                        f"Successfully sent notifications to {result['sent']} devices. Failed: {result['failed']}"
                    )
                    if result['invalid_tokens']:
                        messages.warning(
                            request,
                            f'{len(result["invalid_tokens"])} devices were deactivated due to invalid tokens'
                        )
                else:
                    error_msg = 'Failed to send push notification'
                    if result['errors']:
                        error_msg += f': {", ".join(result["errors"])}'
                    messages.error(request, error_msg)

                # Clear session
                if 'selected_tokens' in request.session:
                    del request.session['selected_tokens']

                return redirect('admin:notifications_devicetoken_changelist')

            except Exception as e:
                logger.exception('Error in send_push_view')
                messages.error(request, f'Error sending push notification: {str(e)}')
                return TemplateResponse(
                    request,
                    'admin/notifications/devicetoken/send_push.html',
                    context
                )

        return TemplateResponse(
            request,
            'admin/notifications/devicetoken/send_push.html',
            context
        )
    def send_test_notification(self, request, queryset):
        users = list(queryset.values_list('user', flat=True).distinct())
        result = send_push_notification(
            message="Test notification from admin",
            users=users
        )
        
        if result['success']:
            messages.success(
                request, 
                f"Successfully sent notifications to {result['sent']} devices. Failed: {result['failed']}"
            )
        else:
            messages.error(
                request, 
                f"Failed to send notifications. Errors: {', '.join(result['errors'])}"
            )
    
    send_test_notification.short_description = "Send test notification to selected devices"

class PromoEmailAdminForm(forms.ModelForm):
    class Meta:
        model = PromoEmail
        fields = '__all__'

@admin.register(PromoEmail)
class PromoEmailAdmin(admin.ModelAdmin):
    form = PromoEmailAdminForm
    list_display = ('title', 'subject', 'status', 'created_at', 'scheduled_for', 'sent_at', 'recipient_count')
    list_filter = ('status', 'created_at', 'sent_at')
    search_fields = ('title', 'subject', 'content_html', 'content_text')
    readonly_fields = ('created_at', 'updated_at', 'sent_at', 'status')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:id>/send-preview/',
                self.admin_site.admin_view(self.send_preview_view),
                name='notifications_promoemail_send_preview',
            ),
            path(
                '<int:id>/view-rendered/',
                self.admin_site.admin_view(self.view_rendered_view),
                name='notifications_promoemail_view_rendered',
            ),
            path(
                '<int:id>/schedule/',
                self.admin_site.admin_view(self.schedule_view),
                name='notifications_promoemail_schedule',
            ),
        ]
        return custom_urls + urls

    def add_view(self, request, form_url='', extra_context=None):
        extra_context = extra_context or {}
        extra_context.update({
            'show_custom_buttons': False
        })
        return super().add_view(request, form_url, extra_context)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        extra_context.update({
            'show_custom_buttons': True
        })
        return super().change_view(request, object_id, form_url, extra_context)

    def send_preview_view(self, request, id):
        """View for sending preview emails."""
        # First check if the request method is POST
        if request.method == 'POST':
            # Get the object using the correct parameter
            promo = self.get_object(request, id)
            if promo is None:
                # Handle case where object doesn't exist
                messages.error(request, "Promo email not found")
                return redirect('admin:notifications_promoemail_changelist')
                
            email = request.POST.get('email')
            result = promo.send_preview(email)
            
            if result['success']:
                messages.success(request, f"Preview email sent to {result['to']}")
                return redirect('admin:notifications_promoemail_change', id)
            else:
                messages.error(request, f"Failed to send preview: {result['error']}")
        
        # For GET requests, get the object after checking the method
        promo = self.get_object(request, id)
        if promo is None:
            # Handle case where object doesn't exist
            messages.error(request, "Promo email not found")
            return redirect('admin:notifications_promoemail_changelist')
        
        context = {
            'promo': promo,
            'opts': self.model._meta,
            'title': 'Send Preview Email',
            'site_url': settings.FRONTEND_URL,
            'has_permission': True,
            'is_popup': False,
            'save_as': False,
            'has_delete_permission': False,
            'has_add_permission': False,
            'has_change_permission': True,
        }
        return TemplateResponse(request, 'admin/send_preview_email.html', context)
    
    def view_rendered_view(self, request, id):
        """View for viewing rendered email."""
        promo = self.get_object(request, id)
        if promo is None:
            # Handle case where object doesn't exist
            messages.error(request, "Promo email not found")
            return redirect('admin:notifications_promoemail_changelist')
        
        # Use admin as the preview user
        from django.contrib.auth import get_user_model
        User = get_user_model()
        admin_user = User.objects.filter(is_staff=True).first()
        
        # Create unsubscribe URL for preview
        unsubscribe_token = f"0:preview"
        unsubscribe_url = f"{settings.BACKEND_URL}{reverse('notifications:unsubscribe')}?token={unsubscribe_token}"
        
        # Prepare email content with preview markers
        context = {
            'user': admin_user,
            'email_content': f"[PREVIEW] {promo.content_html}",
            'unsubscribe_url': unsubscribe_url,
            'site_url': settings.FRONTEND_URL
        }
        
        # Render the email template
        rendered_email = render_to_string('email/promotional_email.html', context)
        
        return TemplateResponse(request, 'admin/view_rendered_email.html', {
            'promo': promo,
            'opts': self.model._meta,
            'rendered_email': rendered_email,
            'title': 'View Rendered Email',
            'site_url': settings.FRONTEND_URL,
            'has_permission': True,
            'is_popup': False,
            'save_as': False,
            'has_delete_permission': False,
            'has_add_permission': False,
            'has_change_permission': True,
        })
    
    def schedule_view(self, request, id):
        """View for scheduling emails."""
        promo = self.get_object(request, id)
        if promo is None:
            # Handle case where object doesn't exist
            messages.error(request, "Promo email not found")
            return redirect('admin:notifications_promoemail_changelist')
        
        if request.method == 'POST':
            scheduled_for = request.POST.get('scheduled_for')
            try:
                promo.scheduled_for = timezone.datetime.strptime(scheduled_for, '%Y-%m-%dT%H:%M')
                promo.status = PromoEmail.SCHEDULED
                promo.save()
                
                messages.success(request, f"Email scheduled for {scheduled_for}")
                return redirect('admin:notifications_promoemail_changelist')
            except (ValueError, IndexError):
                messages.error(request, "Invalid date format")
        
        context = {
            'promo': promo,
            'opts': self.model._meta,
            'title': 'Schedule Email',
            'site_url': settings.FRONTEND_URL,
            'has_permission': True,
            'is_popup': False,
            'save_as': False,
            'has_delete_permission': False,
            'has_add_permission': False,
            'has_change_permission': True,
        }
        return TemplateResponse(request, 'admin/schedule_email.html', context)
    
    def send_now(self, request, queryset):
        """Action for sending emails immediately."""
        for promo in queryset:
            send_promo_email_task.delay(promo.id)
            messages.success(request, f"Started sending '{promo.title}' to {promo.recipient_count()} recipients")
    send_now.short_description = "Send selected emails now"
    
    actions = ['send_now']
