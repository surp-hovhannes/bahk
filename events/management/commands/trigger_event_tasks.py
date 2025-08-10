"""
Management command to trigger event tracking tasks manually.
"""

from django.core.management.base import BaseCommand
from events.tasks import (
    track_fast_participant_milestone_task,
    track_fast_beginning_task,
    check_fast_beginning_events_task,
    check_participation_milestones_task,
    check_devotional_availability_task,
    track_devotional_availability_task,
    track_article_published_task,
    track_recipe_published_task,
    track_video_published_task
)


class Command(BaseCommand):
    help = 'Trigger event tracking tasks manually'

    def add_arguments(self, parser):
        parser.add_argument(
            '--task',
            type=str,
            choices=['milestone', 'beginning', 'check-beginning', 'check-milestones', 'check-devotionals', 'devotional', 'article', 'recipe', 'video'],
            required=True,
            help='Type of task to trigger'
        )
        parser.add_argument(
            '--fast-id',
            type=int,
            help='Fast ID (required for milestone, beginning, and devotional tasks)'
        )
        parser.add_argument(
            '--devotional-id',
            type=int,
            help='Devotional ID (required for devotional task)'
        )
        parser.add_argument(
            '--article-id',
            type=int,
            help='Article ID (required for article task)'
        )
        parser.add_argument(
            '--recipe-id',
            type=int,
            help='Recipe ID (required for recipe task)'
        )
        parser.add_argument(
            '--video-id',
            type=int,
            help='Video ID (required for video task)'
        )
        parser.add_argument(
            '--participant-count',
            type=int,
            help='Participant count (optional for milestone task)'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run task asynchronously (default: synchronous)'
        )

    def handle(self, *args, **options):
        task_type = options['task']
        fast_id = options['fast_id']
        devotional_id = options['devotional_id']
        article_id = options['article_id']
        recipe_id = options['recipe_id']
        video_id = options['video_id']
        participant_count = options['participant_count']
        run_async = options['async']

        if task_type in ['milestone', 'beginning', 'devotional'] and not fast_id:
            self.stdout.write(
                self.style.ERROR(f'Fast ID is required for {task_type} task')
            )
            return

        if task_type == 'devotional' and not devotional_id:
            self.stdout.write(
                self.style.ERROR('Devotional ID is required for devotional task')
            )
            return

        if task_type == 'article' and not article_id:
            self.stdout.write(
                self.style.ERROR('Article ID is required for article task')
            )
            return

        if task_type == 'recipe' and not recipe_id:
            self.stdout.write(
                self.style.ERROR('Recipe ID is required for recipe task')
            )
            return

        if task_type == 'video' and not video_id:
            self.stdout.write(
                self.style.ERROR('Video ID is required for video task')
            )
            return

        try:
            if task_type == 'milestone':
                if run_async:
                    result = track_fast_participant_milestone_task.delay(
                        fast_id, participant_count
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled milestone tracking task for fast {fast_id} (task ID: {result.id})'
                        )
                    )
                else:
                    result = track_fast_participant_milestone_task(fast_id, participant_count)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Milestone tracking completed for fast {fast_id}: {result}'
                        )
                    )

            elif task_type == 'beginning':
                if run_async:
                    result = track_fast_beginning_task.delay(fast_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled fast beginning tracking task for fast {fast_id} (task ID: {result.id})'
                        )
                    )
                else:
                    result = track_fast_beginning_task(fast_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Fast beginning tracking completed for fast {fast_id}: {result}'
                        )
                    )

            elif task_type == 'devotional':
                if run_async:
                    result = track_devotional_availability_task.delay(fast_id, devotional_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled devotional availability tracking task for fast {fast_id}, devotional {devotional_id} (task ID: {result.id})'
                        )
                    )
                else:
                    result = track_devotional_availability_task(fast_id, devotional_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Devotional availability tracking completed for fast {fast_id}, devotional {devotional_id}: {result}'
                        )
                    )

            elif task_type == 'article':
                if run_async:
                    result = track_article_published_task.delay(article_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled article publication tracking task for article {article_id} (task ID: {result.id})'
                        )
                    )
                else:
                    result = track_article_published_task(article_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Article publication tracking completed for article {article_id}: {result}'
                        )
                    )

            elif task_type == 'recipe':
                if run_async:
                    result = track_recipe_published_task.delay(recipe_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled recipe publication tracking task for recipe {recipe_id} (task ID: {result.id})'
                        )
                    )
                else:
                    result = track_recipe_published_task(recipe_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Recipe publication tracking completed for recipe {recipe_id}: {result}'
                        )
                    )

            elif task_type == 'video':
                if run_async:
                    result = track_video_published_task.delay(video_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled video publication tracking task for video {video_id} (task ID: {result.id})'
                        )
                    )
                else:
                    result = track_video_published_task(video_id)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Video publication tracking completed for video {video_id}: {result}'
                        )
                    )

            elif task_type == 'check-beginning':
                if run_async:
                    result = check_fast_beginning_events_task.delay()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled fast beginning check task (task ID: {result.id})'
                        )
                    )
                else:
                    result = check_fast_beginning_events_task()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Fast beginning check completed: {result}'
                        )
                    )

            elif task_type == 'check-milestones':
                if run_async:
                    result = check_participation_milestones_task.delay()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled participation milestones check task (task ID: {result.id})'
                        )
                    )
                else:
                    result = check_participation_milestones_task()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Participation milestones check completed: {result}'
                        )
                    )

            elif task_type == 'check-devotionals':
                if run_async:
                    result = check_devotional_availability_task.delay()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Scheduled devotional availability check task (task ID: {result.id})'
                        )
                    )
                else:
                    result = check_devotional_availability_task()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Devotional availability check completed: {result}'
                        )
                    )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error executing task: {e}')
            ) 