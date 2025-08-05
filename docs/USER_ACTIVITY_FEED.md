# User Activity Feed System

## Overview

The User Activity Feed system provides a unified way for users to view all their relevant activities in one place, with read/unread tracking. This includes events like joining/leaving fasts, fast reminders, devotional availability, and milestones.

## Features

### 📱 **Unified Activity Feed**
- Single endpoint for all user-relevant activities
- Combines events, fast reminders, devotional notifications, and milestones
- Chronological ordering with most recent first

### 👁️ **Read/Unread Tracking**
- Track what users have and haven't seen
- Mark individual items or all items as read
- Timestamp tracking for when items were read

### 🎯 **Activity Types**
- **Event-based**: `fast_join`, `fast_leave`, `fast_start`, `devotional_available`, `milestone`
- **Reminder-based**: `fast_reminder`, `devotional_reminder`
- **System-generated**: Automatic creation from events and notifications

### 🔍 **Filtering & Search**
- Filter by activity type
- Filter by read/unread status
- Date range filtering
- Pagination support

## 📊 **Table Growth & Data Retention**

### **Growth Projections**

The UserActivityFeed table will grow significantly as your user base expands:

| User Count | Events/User/Month | Feed Items/User/Month | Monthly Growth | Yearly Growth | Storage (GB) |
|------------|-------------------|----------------------|----------------|---------------|--------------|
| 1,000      | 5                 | 5                    | 5,000          | 60,000        | 0.04         |
| 10,000     | 3                 | 3                    | 30,000         | 360,000       | 0.25         |
| 100,000    | 2                 | 2                    | 200,000        | 2.4M          | 1.7          |
| 1M         | 1                 | 1                    | 1M             | 12M           | 8.4          |

### **Data Retention Policies**

The system includes configurable retention policies to manage table growth:

```python
RETENTION_POLICY = {
    'fast_reminder': 30,      # Keep reminders for 30 days
    'devotional_reminder': 30, # Keep devotional reminders for 30 days
    'fast_start': 90,         # Keep fast starts for 90 days
    'fast_join': 180,         # Keep join events for 6 months
    'fast_leave': 180,        # Keep leave events for 6 months
    'devotional_available': 90, # Keep devotional notifications for 90 days
    'milestone': 365,         # Keep milestones for 1 year
    'event': 180,             # Keep generic events for 6 months
}
```

### **Cleanup Strategies**

#### 1. **Automatic Cleanup**
```bash
# Clean up old items based on retention policy
python manage.py cleanup_activity_feeds

# Dry run to see what would be deleted
python manage.py cleanup_activity_feeds --dry-run

# Clean up specific activity type
python manage.py cleanup_activity_feeds --activity-type fast_reminder

# Override retention policy
python manage.py cleanup_activity_feeds --older-than-days 60
```

#### 2. **Scheduled Cleanup**
Set up a cron job or Celery task to run cleanup regularly:

```python
# In your Celery tasks
@shared_task
def cleanup_old_activity_feeds():
    from events.models import UserActivityFeed
    deleted_count = UserActivityFeed.cleanup_old_items(dry_run=False)
    logger.info(f"Cleaned up {deleted_count} old activity feed items")
```

#### 3. **Archiving Strategy**
For long-term data preservation, archive old items instead of deleting:

```python
# Archive items older than 1 year
archived_count, deleted_count = UserActivityFeed.archive_old_items(
    archive_older_than_days=365
)
```

### **Performance Optimizations**

#### **Database Indexes**
The model includes optimized indexes for common queries:
```python
indexes = [
    models.Index(fields=['user', 'is_read']),
    models.Index(fields=['user', 'activity_type']),
    models.Index(fields=['user', 'created_at']),
    models.Index(fields=['created_at', 'is_read']),      # For cleanup queries
    models.Index(fields=['activity_type', 'created_at']), # For type-specific cleanup
]
```

#### **Query Optimization**
- Uses `select_related` for efficient joins
- Pagination to limit result sets
- Date range filtering to reduce query scope
- Optimized cleanup queries

#### **Monitoring & Analytics**
```python
# Get user feed statistics
stats = UserActivityFeed.get_user_feed_stats(user)
print(f"User has {stats['total_items']} items, {stats['unread_count']} unread")

# Monitor table growth
total_items = UserActivityFeed.objects.count()
unread_items = UserActivityFeed.objects.filter(is_read=False).count()
```

### **Scaling Strategies**

#### **Phase 1: Basic Cleanup (Current)**
- Retention policies
- Regular cleanup jobs
- Optimized queries

#### **Phase 2: Partitioning (10K+ users)**
```python
# Partition by user_id or date
class UserActivityFeedPartitioned(UserActivityFeed):
    class Meta:
        proxy = True
        # Partition by user_id ranges or date ranges
```

#### **Phase 3: Separate Storage (100K+ users)**
- Move old data to separate database
- Use read replicas for analytics
- Implement data archival system

#### **Phase 4: Time-Series Database (1M+ users)**
- Consider InfluxDB or TimescaleDB
- Implement data retention at database level
- Use specialized time-series optimizations

## API Endpoints

### 1. Get User Activity Feed
```
GET /api/events/activity-feed/
```

**Query Parameters:**
- `activity_type` - Filter by activity type (e.g., `fast_join`, `devotional_reminder`)
- `is_read` - Filter by read status (`true`/`false`)
- `start_date` - ISO format date (e.g., `2024-01-01T00:00:00`)
- `end_date` - ISO format date
- `page` - Page number for pagination

**Response:**
```json
{
  "count": 25,
  "next": "http://api.example.com/events/activity-feed/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "activity_type": "fast_join",
      "activity_type_display": "Joined Fast",
      "title": "You joined the Great Lent Fast",
      "description": "You joined the Great Lent Fast",
      "is_read": false,
      "read_at": null,
      "created_at": "2024-01-15T10:30:00Z",
      "age_display": "2h ago",
      "data": {
        "fast_id": 123,
        "fast_name": "Great Lent Fast"
      },
      "target_type": "hub.fast",
      "target_id": 123
    }
  ]
}
```

### 2. Get Activity Feed Summary
```
GET /api/events/activity-feed/summary/
```

**Response:**
```json
{
  "total_items": 25,
  "unread_count": 8,
  "read_count": 17,
  "activity_types": {
    "fast_join": 5,
    "fast_reminder": 3,
    "devotional_reminder": 2,
    "milestone": 1
  },
  "recent_activity": [
    // Last 5 activity items
  ]
}
```

### 3. Mark Items as Read
```
POST /api/events/activity-feed/mark-read/
```

**Request Body Options:**

**Mark specific items:**
```json
{
  "activity_ids": [1, 2, 3, 4, 5]
}
```

**Mark all unread items:**
```json
{
  "mark_all": true
}
```

**Response:**
```json
{
  "message": "Marked 5 items as read",
  "updated_count": 5
}
```

### 4. Generate Activity Feed (Admin Only)
```
POST /api/events/activity-feed/generate/
```

**Request Body:**
```json
{
  "user_id": 123,
  "days_back": 30
}
```

**Response:**
```json
{
  "message": "Generated 15 activity feed items for user john_doe",
  "created_count": 15,
  "date_range": {
    "start": "2023-12-15T00:00:00Z",
    "end": "2024-01-15T00:00:00Z"
  }
}
```

## Data Model

### UserActivityFeed Model

```python
class UserActivityFeed(models.Model):
    ACTIVITY_TYPES = [
        ('event', 'Event'),
        ('fast_start', 'Fast Started'),
        ('fast_join', 'Joined Fast'),
        ('fast_leave', 'Left Fast'),
        ('devotional_available', 'Devotional Available'),
        ('milestone', 'Milestone Reached'),
        ('fast_reminder', 'Fast Reminder'),
        ('devotional_reminder', 'Devotional Reminder'),
    ]
    
    user = models.ForeignKey(User, ...)
    activity_type = models.CharField(choices=ACTIVITY_TYPES, ...)
    event = models.ForeignKey(Event, null=True, ...)  # If event-based
    target = GenericForeignKey(...)  # Fast, Devotional, etc.
    title = models.CharField(...)
    description = models.TextField(...)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    data = models.JSONField(default=dict)
```

## Automatic Feed Item Creation

### Event-Based Creation
When events are created, activity feed items are automatically generated:

```python
# Signal handler automatically creates feed items
@receiver(post_save, sender=Event)
def create_activity_feed_item(sender, instance, created, **kwargs):
    if created and instance.user:
        UserActivityFeed.create_from_event(instance, instance.user)
```

### Manual Creation
You can also create feed items manually:

```python
# Create from event
feed_item = UserActivityFeed.create_from_event(event, user)

# Create fast reminder
feed_item = UserActivityFeed.create_fast_reminder(user, fast)

# Create devotional reminder
feed_item = UserActivityFeed.create_devotional_reminder(user, devotional, fast)
```

## 🔄 **Hybrid Sync/Async Approach**

The system supports both synchronous and asynchronous activity feed creation, allowing you to choose the best approach for your needs.

### **Configuration**

Set the behavior via Django settings:

```python
# settings.py
USE_ASYNC_ACTIVITY_FEED = True  # Use Celery (async)
USE_ASYNC_ACTIVITY_FEED = False # Use signals (sync, default)
```

Or via environment variable:
```bash
USE_ASYNC_ACTIVITY_FEED=true
```

### **Synchronous Creation (Default)**

**How it works:**
- Feed items created immediately in the same request
- Guaranteed consistency
- Simpler debugging
- No additional infrastructure needed

**Best for:**
- Low to moderate user volume
- When immediate consistency is required
- Development and testing

```python
# Feed item created immediately
user.join_fast()  # Feed item appears instantly
```

### **Asynchronous Creation (Celery)**

**How it works:**
- Feed items created in background via Celery
- Better user experience (faster responses)
- Handles high volume gracefully
- Built-in retry logic

**Best for:**
- High user volume (1000+ concurrent users)
- When you want to optimize response times
- Production environments with Celery infrastructure

```python
# Feed item created in background
user.join_fast()  # Returns immediately
# Feed item created via Celery task
```

### **Celery Tasks Available**

```python
# Create single feed item
from events.tasks import create_activity_feed_item_task
create_activity_feed_item_task.delay(event_id, user_id)

# Create reminder feed items for all users in a fast
from events.tasks import create_fast_reminder_feed_items_task
create_fast_reminder_feed_items_task.delay(fast_id, 'fast_reminder')

# Create devotional reminder feed items
from events.tasks import create_devotional_reminder_feed_items_task
create_devotional_reminder_feed_items_task.delay(devotional_id, fast_id)

# Batch create multiple feed items
from events.tasks import batch_create_activity_feed_items_task
batch_create_activity_feed_items_task.delay([event_id1, event_id2, event_id3])

# Clean up old items
from events.tasks import cleanup_old_activity_feed_items_task
cleanup_old_activity_feed_items_task.delay()

# Populate user's feed with historical data
from events.tasks import populate_user_activity_feed_task
populate_user_activity_feed_task.delay(user_id, days_back=30)
```

### **Scheduled Tasks**

The system includes automatic scheduled tasks:

```python
# Daily cleanup at 2 AM
'cleanup-old-activity-feed-items-daily': {
    'task': 'events.tasks.cleanup_old_activity_feed_items_task',
    'schedule': crontab(hour=2, minute=0),
}
```

### **Error Handling & Retries**

Celery tasks include robust error handling:

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def create_activity_feed_item_task(self, event_id, user_id=None):
    try:
        # Create feed item
        pass
    except Exception as exc:
        # Retry up to 3 times with 60-second delays
        raise self.retry(exc=exc)
```

### **Monitoring**

Monitor task performance via Sentry:

```python
'options': {
    'sentry': {
        'monitor_slug': 'daily-activity-feed-cleanup',
    }
}
```

### **Migration Strategy**

**Phase 1: Start with Synchronous**
- Use default behavior (synchronous)
- Monitor performance and user volume
- Ensure feed creation is working correctly

**Phase 2: Enable Async When Needed**
- Set `USE_ASYNC_ACTIVITY_FEED = True`
- Monitor Celery queue performance
- Ensure proper error handling

**Phase 3: Optimize**
- Use batch operations for bulk creation
- Implement custom retry strategies
- Add performance monitoring

## Management Commands

### Populate Historical Data
```bash
# Populate all users' feeds for last 30 days
python manage.py populate_activity_feeds

# Populate specific user
python manage.py populate_activity_feeds --user-id 123

# Populate for last 90 days
python manage.py populate_activity_feeds --days-back 90

# Dry run to see what would be created
python manage.py populate_activity_feeds --dry-run
```

### Cleanup Old Data
```bash
# Clean up based on retention policies
python manage.py cleanup_activity_feeds

# Dry run to see what would be deleted
python manage.py cleanup_activity_feeds --dry-run

# Clean up specific activity type
python manage.py cleanup_activity_feeds --activity-type fast_reminder

# Override retention policy
python manage.py cleanup_activity_feeds --older-than-days 60
```

## Frontend Integration

### Example React/JavaScript Usage

```javascript
// Get user's activity feed
const getActivityFeed = async (filters = {}) => {
  const params = new URLSearchParams(filters);
  const response = await fetch(`/api/events/activity-feed/?${params}`);
  return response.json();
};

// Mark items as read
const markAsRead = async (activityIds) => {
  const response = await fetch('/api/events/activity-feed/mark-read/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ activity_ids: activityIds })
  });
  return response.json();
};

// Mark all as read
const markAllAsRead = async () => {
  const response = await fetch('/api/events/activity-feed/mark-read/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mark_all: true })
  });
  return response.json();
};

// Get summary
const getSummary = async () => {
  const response = await fetch('/api/events/activity-feed/summary/');
  return response.json();
};
```

### Example Feed Component

```jsx
function ActivityFeed() {
  const [feed, setFeed] = useState([]);
  const [summary, setSummary] = useState({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadFeed();
    loadSummary();
  }, []);

  const loadFeed = async () => {
    const data = await getActivityFeed({ is_read: 'false' });
    setFeed(data.results);
    setLoading(false);
  };

  const handleMarkAsRead = async (activityId) => {
    await markAsRead([activityId]);
    loadFeed(); // Refresh feed
    loadSummary(); // Refresh summary
  };

  return (
    <div className="activity-feed">
      <div className="feed-header">
        <h2>Activity Feed</h2>
        <div className="summary">
          <span>{summary.unread_count} unread</span>
          <button onClick={() => markAllAsRead()}>
            Mark All Read
          </button>
        </div>
      </div>
      
      {feed.map(item => (
        <div 
          key={item.id} 
          className={`feed-item ${item.is_read ? 'read' : 'unread'}`}
          onClick={() => handleMarkAsRead(item.id)}
        >
          <div className="activity-type">{item.activity_type_display}</div>
          <div className="title">{item.title}</div>
          <div className="description">{item.description}</div>
          <div className="age">{item.age_display}</div>
        </div>
      ))}
    </div>
  );
}
```

## Performance Considerations

### Database Indexes
The model includes optimized indexes for common queries:
- `(user, is_read)` - For filtering unread items
- `(user, activity_type)` - For filtering by activity type
- `(user, created_at)` - For chronological ordering
- `(created_at, is_read)` - For cleanup queries
- `(activity_type, created_at)` - For type-specific cleanup

### Query Optimization
- Uses `select_related` for efficient joins
- Pagination to limit result sets
- Date range filtering to reduce query scope

### Caching Strategy
Consider implementing Redis caching for:
- User's unread count
- Recent activity items
- Activity type summaries

## Migration Strategy

### For Existing Users
1. Run the migration: `python manage.py migrate`
2. Populate historical data: `python manage.py populate_activity_feeds`
3. Update your frontend to use the new endpoints

### For New Users
- Activity feed items are created automatically when events occur
- No additional setup required

## Future Enhancements

### Potential Additions
- **Real-time Updates**: WebSocket integration for live feed updates
- **Push Notifications**: Integrate with existing push notification system
- **Activity Preferences**: Let users choose which activity types to see
- **Activity Actions**: Allow users to take actions directly from feed items
- **Activity Analytics**: Track user engagement with feed items
- **Smart Filtering**: AI-powered relevance scoring for feed items

### Performance Optimizations
- **Background Processing**: Use Celery for feed item creation
- **Database Partitioning**: Partition by user for large-scale deployments
- **CDN Integration**: Cache feed data at the edge
- **Elasticsearch**: For advanced search and filtering capabilities 