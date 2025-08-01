"""
Django management command to check the status of Celery tasks.

This command allows checking the status of running or completed tasks,
particularly useful for monitoring long-running bookmark cleanup operations.

Usage:
    python manage.py check_task_status <task_id>                    # Check task status
    python manage.py check_task_status <task_id> --wait             # Wait for completion
    python manage.py check_task_status <task_id> --result           # Show full result
"""

from django.core.management.base import BaseCommand, CommandError
import time
import json


class Command(BaseCommand):
    help = 'Check the status of a Celery task'

    def add_arguments(self, parser):
        parser.add_argument(
            'task_id',
            type=str,
            help='The ID of the task to check'
        )
        
        parser.add_argument(
            '--wait',
            action='store_true',
            help='Wait for the task to complete'
        )
        
        parser.add_argument(
            '--result',
            action='store_true',
            help='Show the full task result (only for completed tasks)'
        )
        
        parser.add_argument(
            '--json',
            action='store_true',
            help='Output result as JSON'
        )

    def handle(self, *args, **options):
        task_id = options['task_id']
        wait_for_completion = options['wait']
        show_result = options['result']
        json_output = options['json']
        
        try:
            from celery.result import AsyncResult
        except ImportError:
            raise CommandError("Celery is not available. Please ensure Celery is installed.")
        
        # Get task result object
        task = AsyncResult(task_id)
        
        if not wait_for_completion:
            # Show current status
            self._show_task_status(task, show_result, json_output)
        else:
            # Wait for completion
            self._wait_for_task(task, show_result, json_output)

    def _show_task_status(self, task, show_result=False, json_output=False):
        """Show the current status of a task."""
        status_info = {
            'task_id': task.id,
            'status': task.status,
            'ready': task.ready()
        }
        
        if json_output:
            # Add additional info for JSON output
            if hasattr(task, 'result') and task.result:
                if isinstance(task.result, dict):
                    status_info.update(task.result)
                else:
                    status_info['result'] = str(task.result)
            
            if task.status == 'FAILURE' and hasattr(task, 'info'):
                status_info['error'] = str(task.info)
            
            self.stdout.write(json.dumps(status_info, indent=2, default=str))
            return
        
        # Human-readable output
        self.stdout.write(f"📋 Task ID: {task.id}")
        self.stdout.write(f"📊 Status: {self._format_status(task.status)}")
        self.stdout.write(f"✅ Ready: {'Yes' if task.ready() else 'No'}")
        
        # Show progress if available
        if not task.ready() and hasattr(task, 'result') and isinstance(task.result, dict):
            result = task.result
            if 'current' in result and 'total' in result:
                current = result['current']
                total = result['total']
                progress = result.get('progress_percent', 0)
                self.stdout.write(f"📈 Progress: {current}/{total} ({progress}%)")
            
            if 'status' in result:
                self.stdout.write(f"🔄 Current: {result['status']}")
        
        # Show result for completed tasks
        if task.ready():
            if task.successful() and show_result:
                try:
                    result = task.get()
                    self._show_task_result(result)
                except Exception as e:
                    self.stdout.write(f"❌ Error getting result: {e}")
            elif task.status == 'FAILURE':
                self.stdout.write(f"❌ Error: {task.info}")

    def _wait_for_task(self, task, show_result=False, json_output=False):
        """Wait for a task to complete and show progress."""
        if json_output:
            # For JSON output, just wait and show final result
            while not task.ready():
                time.sleep(1)
            self._show_task_status(task, show_result, json_output)
            return
        
        self.stdout.write(f"⏳ Waiting for task {task.id} to complete...")
        self.stdout.write("   Press Ctrl+C to stop waiting (task will continue running)")
        
        last_progress = -1
        
        try:
            while not task.ready():
                # Show progress if available
                try:
                    if hasattr(task, 'result') and isinstance(task.result, dict):
                        result = task.result
                        if 'progress_percent' in result:
                            progress = result['progress_percent']
                            if progress > last_progress:
                                current = result.get('current', 0)
                                total = result.get('total', 0)
                                self.stdout.write(f"   Progress: {current}/{total} ({progress}%)")
                                last_progress = progress
                except Exception:
                    # Task might not have progress info yet
                    pass
                
                time.sleep(2)
            
            # Task completed
            self.stdout.write("\n✅ Task completed!")
            self._show_task_status(task, show_result, json_output)
            
        except KeyboardInterrupt:
            self.stdout.write(f"\n⚠️  Stopped waiting. Task {task.id} is still running.")
            self.stdout.write(f"   Check status later with: python manage.py check_task_status {task.id}")

    def _show_task_result(self, result):
        """Show detailed task result."""
        if not isinstance(result, dict):
            self.stdout.write(f"📄 Result: {result}")
            return
        
        self.stdout.write("\n📋 Task Result:")
        self.stdout.write("-" * 20)
        
        # Format common fields nicely
        if 'duration_seconds' in result:
            self.stdout.write(f"⏱️  Duration: {result['duration_seconds']:.2f} seconds")
        
        if 'total_processed' in result:
            self.stdout.write(f"📈 Processed: {result['total_processed']}")
        
        if 'orphaned_found' in result:
            self.stdout.write(f"🔍 Orphaned Found: {result['orphaned_found']}")
        
        if 'deleted' in result:
            self.stdout.write(f"🗑️  Deleted: {result['deleted']}")
        
        if result.get('dry_run'):
            self.stdout.write("🧪 Mode: DRY RUN")
        
        # Show breakdown by type
        if 'orphaned_by_type' in result and result['orphaned_by_type']:
            self.stdout.write("\n📊 Breakdown by type:")
            for content_type, count in result['orphaned_by_type'].items():
                self.stdout.write(f"  {content_type.title()}: {count}")

    def _format_status(self, status):
        """Format task status with appropriate emoji."""
        status_map = {
            'PENDING': '⏳ Pending',
            'STARTED': '🚀 Started',
            'PROGRESS': '📈 In Progress',
            'SUCCESS': '✅ Success',
            'FAILURE': '❌ Failed',
            'RETRY': '🔄 Retrying',
            'REVOKED': '🚫 Revoked'
        }
        return status_map.get(status, f"❓ {status}")