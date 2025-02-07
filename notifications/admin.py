from django.contrib import admin
from django.contrib import messages
from django.template.response import TemplateResponse
from django.urls import path
from .models import DeviceToken
from .utils import send_push_notification

# Register your models here.

@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ('token', 'user', 'created_at', 'is_active', 'last_used')
    search_fields = ('token', 'user__email')
    ordering = ('-created_at',)
    actions = ['send_push_notification', 'send_test_notification']
    list_filter = ('is_active', 'created_at', 'last_used')

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
            data = request.POST.get('data', '{}')
            selected_ids = request.session.get('selected_tokens', [])
            
            if not message:
                messages.error(request, 'Message is required')
                return TemplateResponse(
                    request,
                    'admin/notifications/devicetoken/send_push.html',
                    context
                )

            try:
                tokens = DeviceToken.objects.filter(
                    id__in=selected_ids,
                    is_active=True
                ).values_list('token', flat=True)
                
                if not tokens:
                    messages.error(request, 'No active tokens selected')
                    return TemplateResponse(
                        request,
                        'admin/notifications/devicetoken/send_push.html',
                        context
                    )

                result = send_push_notification(
                    message=message,
                    data=eval(data) if data else None,
                    tokens=list(tokens)
                )

                if result:
                    messages.success(
                        request,
                        f'Successfully sent push notification to {len(tokens)} devices'
                    )
                else:
                    messages.error(request, 'Failed to send push notification')

            except Exception as e:
                messages.error(request, f'Error sending push notification: {str(e)}')

            # Clear session
            if 'selected_tokens' in request.session:
                del request.session['selected_tokens']

            return self.response_post_save_change(request, None)

        return TemplateResponse(
            request,
            'admin/notifications/devicetoken/send_push.html',
            context
        )

    def send_test_notification(self, request, queryset):
        tokens = list(queryset.values_list('token', flat=True))
        result = send_push_notification(
            message="Test notification from admin",
            tokens=tokens
        )
        
        # Log the result
        print(f"Push notification result: {result}")  # Debug logging
        
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
