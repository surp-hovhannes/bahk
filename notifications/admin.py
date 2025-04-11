from django.contrib import admin
from django.contrib import messages
from django.template.response import TemplateResponse
from django.urls import path
from django.shortcuts import redirect
from .models import DeviceToken
from .utils import send_push_notification
from .tasks import send_push_notification_task
from django.contrib.admin import SimpleListFilter
from hub.models import Fast
from django.utils import timezone
from django.db.models import Q


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
    list_display = ('token', 'user', 'created_at', 'is_active', 'last_used')
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
