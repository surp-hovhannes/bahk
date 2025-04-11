from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.core.cache import cache
from hub.models import Profile, Fast

@receiver(m2m_changed, sender=Profile.fasts.through)
def handle_fast_participant_change(sender, instance, action, **kwargs):
    """
    Signal handler that invalidates the FastListView cache when
    participants join or leave fasts.
    
    This triggers on any change to the many-to-many relationship
    between Profile and Fast models.
    """
    # Only proceed for these specific actions
    if action in ('post_add', 'post_remove', 'post_clear'):
        # Determine the church ID based on the instance type
        church_id = None
        
        if isinstance(instance, Profile):
            # This is a Profile instance, get its church ID
            church_id = instance.church_id
            #print(f"Profile {instance.id} modified fasts, church_id: {church_id}")
        else:
            # This is a Fast instance, get its church ID
            church_id = instance.church_id
            # print(f"Fast {instance.id} modified profiles, church_id: {church_id}")
        
        if church_id:
            # Invalidate the participant count cache
            cache.delete(f'church_{church_id}_participant_count')
            # print(f"Invalidated participant count cache for church {church_id}")
            
            # Also clear all cached querysets for this church
            pattern = f'fast_list_qs:{church_id}:*'
            # Note: If your cache backend doesn't support pattern matching, 
            # this will need a different approach
            keys = cache.keys(pattern) if hasattr(cache, 'keys') else []
            if keys:
                #print(f"Clearing {len(keys)} cached querysets for church {church_id}")
                cache.delete_many(keys)
