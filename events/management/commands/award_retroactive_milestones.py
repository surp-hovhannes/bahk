"""
Management command to award retroactive milestones to existing users.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from events.models import UserMilestone
from hub.models import Fast, Profile
from notifications.utils import is_weekly_fast

User = get_user_model()


class Command(BaseCommand):
    help = 'Award retroactive milestones to existing users based on their fast history'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='Award milestones for specific user by ID'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually doing it'
        )
        parser.add_argument(
            '--milestone-type',
            choices=['first_fast_join', 'first_nonweekly_fast_complete', 'all'],
            default='all',
            help='Which milestone type to award (default: all)'
        )

    def handle(self, *args, **options):
        user_id = options.get('user_id')
        dry_run = options.get('dry_run')
        milestone_type = options.get('milestone_type')

        if user_id:
            try:
                user = User.objects.get(id=user_id)
                users = [user]
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"User {user_id} not found")
                )
                return
        else:
            # Get all active users with profiles
            users = User.objects.filter(
                is_active=True,
                profile__isnull=False
            ).select_related('profile')

        self.stdout.write(
            self.style.SUCCESS(f"Processing {len(users)} users for retroactive milestones...")
        )

        if milestone_type in ['first_fast_join', 'all']:
            self.award_first_fast_join_milestones(users, dry_run)
        
        if milestone_type in ['first_nonweekly_fast_complete', 'all']:
            self.award_first_nonweekly_complete_milestones(users, dry_run)

    def award_first_fast_join_milestones(self, users, dry_run=False):
        """Award first fast join milestones based on earliest fast joined."""
        self.stdout.write(self.style.WARNING("\n=== FIRST FAST JOIN MILESTONES ==="))
        
        awarded_count = 0
        skipped_count = 0
        
        for user in users:
            try:
                # Check if user already has this milestone
                if UserMilestone.objects.filter(
                    user=user,
                    milestone_type='first_fast_join'
                ).exists():
                    skipped_count += 1
                    continue
                
                # Get user's earliest fast by finding the fast with the earliest start date
                user_fasts = user.profile.fasts.annotate(
                    start_date=models.Min('days__date')
                ).order_by('start_date')
                
                if not user_fasts.exists():
                    # User has never joined a fast
                    continue
                
                earliest_fast = user_fasts.first()
                
                if dry_run:
                    self.stdout.write(
                        f"[DRY RUN] Would award first fast join milestone to {user.username} "
                        f"for fast: {earliest_fast.name}"
                    )
                    awarded_count += 1
                else:
                    # Award the milestone with the earliest fast as the related object
                    milestone = UserMilestone.create_milestone(
                        user=user,
                        milestone_type='first_fast_join',
                        related_object=earliest_fast,
                        data={
                            'fast_id': earliest_fast.id,
                            'fast_name': earliest_fast.name,
                            'church_name': earliest_fast.church.name if earliest_fast.church else None,
                            'retroactive': True,
                            'awarded_date': timezone.now().isoformat(),
                        }
                    )
                    
                    if milestone:
                        self.stdout.write(
                            f"✅ Awarded first fast join milestone to {user.username} "
                            f"for fast: {earliest_fast.name}"
                        )
                        awarded_count += 1
                    else:
                        self.stdout.write(
                            f"❌ Failed to award milestone to {user.username}"
                        )
                        
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Error processing user {user.username}: {e}")
                )
                continue
        
        action = "Would award" if dry_run else "Awarded"
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{action} {awarded_count} first fast join milestones, "
                f"skipped {skipped_count} users who already have this milestone"
            )
        )

    def award_first_nonweekly_complete_milestones(self, users, dry_run=False):
        """Award first non-weekly fast completion milestones based on historical data."""
        self.stdout.write(self.style.WARNING("\n=== FIRST NON-WEEKLY FAST COMPLETION MILESTONES ==="))
        
        awarded_count = 0
        skipped_count = 0
        
        for user in users:
            try:
                # Check if user already has this milestone
                if UserMilestone.objects.filter(
                    user=user,
                    milestone_type='first_nonweekly_fast_complete'
                ).exists():
                    skipped_count += 1
                    continue
                
                # Get all fasts this user has participated in that have ended
                today = timezone.now().date()
                user_completed_fasts = Fast.objects.filter(
                    profiles=user.profile,
                    days__date__lt=today  # Only fasts that have ended
                ).annotate(
                    end_date=models.Max('days__date')
                ).filter(
                    end_date__lt=today  # Ensure the fast has actually ended
                ).distinct().order_by('end_date')
                
                # Filter out weekly fasts and find the first non-weekly completed fast
                first_nonweekly_fast = None
                for fast in user_completed_fasts:
                    if not is_weekly_fast(fast):
                        first_nonweekly_fast = fast
                        break
                
                if not first_nonweekly_fast:
                    # User has never completed a non-weekly fast
                    continue
                
                if dry_run:
                    self.stdout.write(
                        f"[DRY RUN] Would award first non-weekly completion milestone to {user.username} "
                        f"for fast: {first_nonweekly_fast.name}"
                    )
                    awarded_count += 1
                else:
                    # Award the milestone
                    milestone = UserMilestone.create_milestone(
                        user=user,
                        milestone_type='first_nonweekly_fast_complete',
                        related_object=first_nonweekly_fast,
                        data={
                            'fast_id': first_nonweekly_fast.id,
                            'fast_name': first_nonweekly_fast.name,
                            'church_name': first_nonweekly_fast.church.name if first_nonweekly_fast.church else None,
                            'retroactive': True,
                            'awarded_date': timezone.now().isoformat(),
                        }
                    )
                    
                    if milestone:
                        self.stdout.write(
                            f"✅ Awarded first non-weekly completion milestone to {user.username} "
                            f"for fast: {first_nonweekly_fast.name}"
                        )
                        awarded_count += 1
                    else:
                        self.stdout.write(
                            f"❌ Failed to award milestone to {user.username}"
                        )
                        
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Error processing user {user.username}: {e}")
                )
                continue
        
        action = "Would award" if dry_run else "Awarded"
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{action} {awarded_count} first non-weekly completion milestones, "
                f"skipped {skipped_count} users who already have this milestone"
            )
        )
