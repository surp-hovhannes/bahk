#!/usr/bin/env python
"""
Django/Celery Memory Debugging Tool

This script tracks memory usage during the startup sequence to help identify
what's causing high memory consumption at worker initialization.

Usage:
    heroku run python memory_debug.py -a yourapp
"""

import os
import sys
import gc
import time
import resource
import importlib
import traceback
from collections import defaultdict

# --------------- Configuration ---------------
# Set your Django settings module here
DJANGO_SETTINGS_MODULE = 'bahk.settings'
# Set your Celery app module here
CELERY_APP_MODULE = 'bahk.celery'

# Start with basic memory tracking
memory_snapshots = []
module_sizes = defaultdict(float)

def get_memory_mb():
    """Get current memory usage in MB"""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

def log_memory(message, force_gc=False):
    """Log current memory usage with a message"""
    if force_gc:
        gc.collect()
    
    mem = get_memory_mb()
    timestamp = time.time()
    
    # Get the last memory if available to calculate diff
    last_mem = memory_snapshots[-1][1] if memory_snapshots else 0
    diff = mem - last_mem
    
    # Store the snapshot
    memory_snapshots.append((timestamp, mem, message, diff))
    
    # Print immediately as well
    diff_str = f"{diff:+.2f} MB" if memory_snapshots else "baseline"
    print(f"MEMORY [{mem:.2f} MB] ({diff_str}): {message}")
    
    return mem

def import_and_track(module_name):
    """Import a module and track memory change"""
    before = get_memory_mb()
    try:
        module = importlib.import_module(module_name)
        after = get_memory_mb()
        diff = after - before
        module_sizes[module_name] = diff
        log_memory(f"Imported {module_name}", force_gc=True)
        return module
    except Exception as e:
        log_memory(f"Error importing {module_name}: {str(e)}")
        return None

# --------------- Main Script ---------------
print("\n" + "="*80)
print(" DJANGO/CELERY MEMORY USAGE PROFILER ")
print("="*80 + "\n")

# Start tracking from the beginning
initial_memory = log_memory("Script started")

# Check Python version and environment
log_memory(f"Python version: {sys.version}")
log_memory(f"Current working directory: {os.getcwd()}")

# Record environment variables that might affect memory
env_vars = ["PYTHONPATH", "DJANGO_SETTINGS_MODULE", "NEW_RELIC_CONFIG_FILE", 
            "NEW_RELIC_ENVIRONMENT", "NEW_RELIC_LICENSE_KEY"]
for var in env_vars:
    if var in os.environ:
        # Mask sensitive values
        value = os.environ[var]
        if var == "NEW_RELIC_LICENSE_KEY" and value:
            value = value[:5] + "..." + value[-5:] if len(value) > 10 else "***"
        log_memory(f"Environment variable {var}={value}")

# Import core packages
log_memory("--- Importing core Python packages ---")
core_modules = ['json', 'requests', 'datetime', 'logging']
for module in core_modules:
    import_and_track(module)

# Import and initialize Django
log_memory("--- Starting Django initialization ---")
os.environ.setdefault('DJANGO_SETTINGS_MODULE', DJANGO_SETTINGS_MODULE)
django_module = import_and_track('django')

log_memory("Before django.setup()")
import django
django.setup()
log_memory("After django.setup()", force_gc=True)

# Track memory usage for each Django app
log_memory("\n--- Django Apps Memory Usage ---")
for app in django.apps.apps.get_app_configs():
    if app.name.startswith('django.'):
        # Skip Django's internal apps to focus on project apps
        continue
    
    log_memory(f"Before importing app: {app.name}")
    try:
        importlib.import_module(app.name)
        log_memory(f"After importing app: {app.name}", force_gc=True)
        
        # Try to import common modules in the app
        for submodule in ['models', 'views', 'admin', 'signals', 'tasks']:
            try:
                submodule_name = f"{app.name}.{submodule}"
                import_and_track(submodule_name)
            except ImportError:
                pass
    except ImportError as e:
        log_memory(f"Error importing {app.name}: {str(e)}")

# Check New Relic
log_memory("\n--- New Relic Agent Memory Usage ---")
try:
    new_relic_before = get_memory_mb()
    import_and_track('newrelic')
    import newrelic.agent
    log_memory("Before newrelic.agent.initialize()")
    newrelic.agent.initialize()
    log_memory("After newrelic.agent.initialize()", force_gc=True)
except ImportError:
    log_memory("New Relic agent not installed")
except Exception as e:
    log_memory(f"Error initializing New Relic: {str(e)}")

# Import Celery and related modules
log_memory("\n--- Celery Memory Usage ---")
import_and_track('celery')
try:
    log_memory(f"Before importing Celery app from {CELERY_APP_MODULE}")
    celery_app_module = importlib.import_module(CELERY_APP_MODULE)
    log_memory(f"After importing Celery app", force_gc=True)
    
    # Check if 'app' attribute exists in the module
    if hasattr(celery_app_module, 'app'):
        log_memory("Before accessing celery app instance")
        app = celery_app_module.app
        log_memory("After accessing celery app instance", force_gc=True)
except Exception as e:
    log_memory(f"Error with Celery app import: {str(e)}")

# Get list of all loaded modules sorted by memory usage
log_memory("\n--- Memory Usage by Module (Top 30) ---")
all_modules = sorted(module_sizes.items(), key=lambda x: x[1], reverse=True)
for i, (module_name, size) in enumerate(all_modules[:30]):
    print(f"{i+1:2d}. {module_name}: {size:.2f} MB")

# Summary of memory usage
total_memory = get_memory_mb()
log_memory("\n--- Memory Usage Summary ---")
log_memory(f"Initial memory: {initial_memory:.2f} MB")
log_memory(f"Final memory: {total_memory:.2f} MB")
log_memory(f"Memory change: {total_memory - initial_memory:.2f} MB")

# Print top 10 memory increases
log_memory("\n--- Top 10 Memory Increases ---")
memory_increases = sorted(
    [(msg, diff) for _, _, msg, diff in memory_snapshots[1:]],
    key=lambda x: x[1],
    reverse=True
)
for i, (msg, diff) in enumerate(memory_increases[:10]):
    print(f"{i+1:2d}. {msg}: {diff:.2f} MB")

print("\n" + "="*80)
print(" MEMORY PROFILING COMPLETE ")
print("="*80 + "\n")
