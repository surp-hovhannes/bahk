# Prayer Requests API Documentation

## Overview

The Prayer Requests feature allows users to submit prayer requests for the community, accept requests from others to pray for them, log daily prayers, and send thank you messages when prayer periods complete. All requests go through automated content moderation using profanity filtering and AI-based review.

## Features

- **User-Generated Content**: Users can submit prayer requests with title, description, and optional image
- **Automated Moderation**: Two-tier moderation (profanity filter + Claude AI) ensures content quality
- **Anonymous Requests**: Users can choose to hide their identity from other users (visible to admins)
- **Duration-Based Expiration**: Requests last 1-7 days and auto-complete when expired
- **Auto-Acceptance**: Requesters automatically accept their own requests upon approval
- **Community Engagement**: Users can accept requests, log daily prayers, and receive notifications
- **Milestone Tracking**: Achievements for creating/accepting requests and consistent prayer
- **Activity Feed Integration**: Creates feed items for key actions and completion notifications

## Authentication

All endpoints require authentication using JWT tokens or session authentication.

**Headers Required:**
```
Authorization: Bearer <jwt_token>
```

## API Endpoints

### Base URL
```
/api/prayer-requests/
```

---

## 1. List Prayer Requests

Get prayer requests. By default, returns approved, active (non-expired) requests. Can be filtered by status.

**Endpoint:** `GET /api/prayer-requests/`

**Query Parameters:**
- `status` (str, optional): Filter by status. Can be a single status or comma-separated multiple statuses. Valid values: `pending_moderation`, `approved`, `rejected`, `completed`, `deleted`. When not provided, defaults to approved, active (non-expired) requests only.

**Example Requests:**
```
GET /api/prayer-requests/
GET /api/prayer-requests/?status=completed
GET /api/prayer-requests/?status=pending_moderation
GET /api/prayer-requests/?status=pending_moderation,completed
```

**Response:** `200 OK`

```json
[
  {
    "id": 1,
    "title": "Pray for my father's surgery",
    "description": "My father is having heart surgery tomorrow. Please pray for the doctors and his recovery.",
    "is_anonymous": false,
    "duration_days": 3,
    "expiration_date": "2025-11-21T15:30:00Z",
    "image": "https://s3.amazonaws.com/bucket/prayer_requests/1/image.jpg",
    "thumbnail_url": "https://s3.amazonaws.com/bucket/cache/thumbnails/1_thumb.jpg",
    "reviewed": true,
    "status": "approved",
    "requester": {
      "id": 5,
      "email": "john@example.com",
      "full_name": "John Doe"
    },
    "created_at": "2025-11-18T15:30:00Z",
    "updated_at": "2025-11-18T15:32:00Z",
    "acceptance_count": 12,
    "prayer_log_count": 45,
    "is_expired": false,
    "has_accepted": false,
    "has_prayed_today": false,
    "is_owner": false
  },
  {
    "id": 2,
    "title": "Guidance for job decision",
    "description": "I'm facing an important career choice and need wisdom.",
    "is_anonymous": true,
    "duration_days": 7,
    "expiration_date": "2025-11-25T10:00:00Z",
    "image": null,
    "thumbnail_url": null,
    "reviewed": true,
    "status": "approved",
    "requester": null,
    "created_at": "2025-11-18T10:00:00Z",
    "updated_at": "2025-11-18T10:02:00Z",
    "acceptance_count": 8,
    "prayer_log_count": 20,
    "is_expired": false,
    "has_accepted": true,
    "has_prayed_today": true,
    "is_owner": false
  }
]
```

**Notes:**
- **Default behavior** (no `status` parameter): Only returns `approved` status requests that haven't expired
- **With `status` parameter**: Returns requests matching the specified status(es), regardless of expiration
- Anonymous requests hide `requester` field (shows `null`)
- `has_accepted`, `has_prayed_today`, and `is_owner` are user-specific flags
- `is_owner` is `true` if the requesting user created the prayer request, `false` otherwise
- Invalid status values result in an empty response

---

## 2. Create Prayer Request

Submit a new prayer request for moderation.

**Endpoint:** `POST /api/prayer-requests/`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**

```json
{
  "title": "Prayer for healing",
  "description": "Please pray for my mother who is recovering from surgery. We trust in God's healing power.",
  "is_anonymous": false,
  "duration_days": 5,
  "image": null
}
```

**With Image Upload:**
```
Content-Type: multipart/form-data

title=Prayer for healing
description=Please pray for my mother...
is_anonymous=false
duration_days=5
image=<file>
```

**Field Constraints:**
- `title`: Required, max 200 characters
- `description`: Required
- `is_anonymous`: Optional, default `false`
- `duration_days`: Required, must be between 1-7
- `image`: Optional, image file (JPG, PNG)

**Response:** `201 Created`

```json
{
  "id": 3,
  "title": "Prayer for healing",
  "description": "Please pray for my mother who is recovering from surgery. We trust in God's healing power.",
  "is_anonymous": false,
  "duration_days": 5,
  "expiration_date": "2025-11-23T16:00:00Z",
  "image": null,
  "thumbnail_url": null,
  "reviewed": false,
  "status": "pending_moderation",
  "requester": {
    "id": 7,
    "email": "sarah@example.com",
    "full_name": "Sarah Johnson"
  },
  "created_at": "2025-11-18T16:00:00Z",
  "updated_at": "2025-11-18T16:00:00Z",
  "acceptance_count": 0,
  "prayer_log_count": 0,
  "is_expired": false,
  "has_accepted": false,
  "has_prayed_today": false
}
```

**Validation Errors:** `400 Bad Request`

```json
{
  "non_field_errors": [
    "You cannot have more than 3 active prayer requests at once."
  ]
}
```

**Moderation Process:**
1. Request is created with `status: "pending_moderation"`
2. Async Celery task triggers content moderation
3. Profanity filter check (immediate rejection if fails)
4. AI moderation check using Claude Sonnet 4.5
5. Status updated to `approved` or `rejected`
6. If rejected, email sent to admin (fastandprayhelp@gmail.com)
7. If approved:
   - Activity feed item created
   - Milestone created (if first request)
   - **Requester automatically accepts their own request** (appears in accepted list, but this self-acceptance is excluded from milestone counts)

---

## 3. Get Prayer Request Details

Retrieve a specific prayer request by ID.

**Endpoint:** `GET /api/prayer-requests/{id}/`

**Response:** `200 OK`

```json
{
  "id": 1,
  "title": "Pray for my father's surgery",
  "description": "My father is having heart surgery tomorrow. Please pray for the doctors and his recovery.",
  "is_anonymous": false,
  "duration_days": 3,
  "expiration_date": "2025-11-21T15:30:00Z",
  "image": "https://s3.amazonaws.com/bucket/prayer_requests/1/image.jpg",
  "thumbnail_url": "https://s3.amazonaws.com/bucket/cache/thumbnails/1_thumb.jpg",
  "reviewed": true,
  "status": "approved",
  "requester": {
    "id": 5,
    "email": "john@example.com",
    "full_name": "John Doe"
  },
  "created_at": "2025-11-18T15:30:00Z",
  "updated_at": "2025-11-18T15:32:00Z",
  "acceptance_count": 12,
  "prayer_log_count": 45,
  "is_expired": false,
  "has_accepted": false,
  "has_prayed_today": false,
  "is_owner": false
}
```

**Error:** `404 Not Found`

```json
{
  "detail": "Not found."
}
```

---

## 4. Update Prayer Request

Update your own prayer request (only if still pending moderation).

**Endpoint:** `PATCH /api/prayer-requests/{id}/`

**Permissions:**
- Must be the requester
- Status must be `pending_moderation`

**Request Body:**

```json
{
  "title": "Updated title",
  "description": "Updated description with more details.",
  "image": null
}
```

**Response:** `200 OK`

```json
{
  "id": 3,
  "title": "Updated title",
  "description": "Updated description with more details.",
  "is_anonymous": false,
  "duration_days": 5,
  "expiration_date": "2025-11-23T16:00:00Z",
  "image": null,
  "thumbnail_url": null,
  "reviewed": false,
  "status": "pending_moderation",
  "requester": {
    "id": 7,
    "email": "sarah@example.com",
    "full_name": "Sarah Johnson"
  },
  "created_at": "2025-11-18T16:00:00Z",
  "updated_at": "2025-11-18T16:15:00Z",
  "acceptance_count": 0,
  "prayer_log_count": 0,
  "is_expired": false,
  "has_accepted": false,
  "has_prayed_today": false,
  "is_owner": true
}
```

**Error:** `400 Bad Request`

```json
{
  "detail": "Prayer requests can only be edited while pending moderation."
}
```

**Error:** `403 Forbidden`

```json
{
  "detail": "You can only edit your own prayer requests."
}
```

---

## 5. Delete Prayer Request

Soft delete your own prayer request (sets status to "deleted").

**Endpoint:** `DELETE /api/prayer-requests/{id}/`

**Permissions:**
- Must be the requester

**Response:** `204 No Content`

**Error:** `403 Forbidden`

```json
{
  "detail": "You can only delete your own prayer requests."
}
```

---

## 6. Accept Prayer Request

Commit to praying for a prayer request.

**Endpoint:** `POST /api/prayer-requests/{id}/accept/`

**Request Body:** None

**Response:** `201 Created`

```json
{
  "id": 15,
  "prayer_request": {
    "id": 1,
    "title": "Pray for my father's surgery",
    "description": "My father is having heart surgery tomorrow...",
    "is_anonymous": false,
    "duration_days": 3,
    "expiration_date": "2025-11-21T15:30:00Z",
    "image": "https://s3.amazonaws.com/bucket/prayer_requests/1/image.jpg",
    "thumbnail_url": "https://s3.amazonaws.com/bucket/cache/thumbnails/1_thumb.jpg",
    "reviewed": true,
    "status": "approved",
    "requester": {
      "id": 5,
      "email": "john@example.com",
      "full_name": "John Doe"
    },
    "created_at": "2025-11-18T15:30:00Z",
    "updated_at": "2025-11-18T15:32:00Z",
    "acceptance_count": 13,
    "prayer_log_count": 45,
    "is_expired": false,
    "has_accepted": true,
    "has_prayed_today": false,
    "is_owner": false
  },
  "user": 8,
  "user_email": "mike@example.com",
  "accepted_at": "2025-11-18T17:00:00Z"
}
```

**Side Effects:**
- Creates `PRAYER_REQUEST_ACCEPTED` event
- Creates activity feed item for requester (if not anonymous)
- Checks for milestones (only for accepting OTHER people's requests):
  - `first_prayer_request_accepted` (1st acceptance of someone else's request)
  - `prayer_warrior_10` (10 acceptances of others' requests)
  - `prayer_warrior_50` (50 acceptances of others' requests)

**Note:** Users cannot manually accept their own prayer requests - they are automatically accepted when approved by moderation.

**Error:** `400 Bad Request`

```json
{
  "detail": "You cannot manually accept your own prayer request. Your request is automatically accepted when approved."
}
```

```json
{
  "detail": "This prayer request is not available for acceptance."
}
```

```json
{
  "detail": "This prayer request has expired."
}
```

```json
{
  "detail": "You have already accepted this prayer request."
}
```

---

## 7. Get Accepted Prayer Requests

Retrieve all prayer requests the current user has accepted.

**Endpoint:** `GET /api/prayer-requests/accepted/`

**Response:** `200 OK`

```json
[
  {
    "id": 1,
    "title": "Pray for my father's surgery",
    "description": "My father is having heart surgery tomorrow...",
    "is_anonymous": false,
    "duration_days": 3,
    "expiration_date": "2025-11-21T15:30:00Z",
    "image": "https://s3.amazonaws.com/bucket/prayer_requests/1/image.jpg",
    "thumbnail_url": "https://s3.amazonaws.com/bucket/cache/thumbnails/1_thumb.jpg",
    "reviewed": true,
    "status": "approved",
    "requester": {
      "id": 5,
      "email": "john@example.com",
      "full_name": "John Doe"
    },
    "created_at": "2025-11-18T15:30:00Z",
    "updated_at": "2025-11-18T15:32:00Z",
    "acceptance_count": 13,
    "prayer_log_count": 45,
    "is_expired": false,
    "has_accepted": true,
    "has_prayed_today": false,
    "is_owner": false
  }
]
```

**Notes:**
- Returns prayer requests regardless of status (approved, completed, etc.)
- Useful for showing user's prayer commitments
- **Includes prayer requests the user created** (automatically accepted upon approval)

---

## 8. Mark Prayer as Prayed

Log that you prayed for a request today.

**Endpoint:** `POST /api/prayer-requests/{id}/mark-prayed/`

**Request Body:** None

**Response:** `201 Created`

```json
{
  "id": 42,
  "prayer_request": {
    "id": 1,
    "title": "Pray for my father's surgery",
    "description": "My father is having heart surgery tomorrow...",
    "is_anonymous": false,
    "duration_days": 3,
    "expiration_date": "2025-11-21T15:30:00Z",
    "image": "https://s3.amazonaws.com/bucket/prayer_requests/1/image.jpg",
    "thumbnail_url": "https://s3.amazonaws.com/bucket/cache/thumbnails/1_thumb.jpg",
    "reviewed": true,
    "status": "approved",
    "requester": {
      "id": 5,
      "email": "john@example.com",
      "full_name": "John Doe"
    },
    "created_at": "2025-11-18T15:30:00Z",
    "updated_at": "2025-11-18T15:32:00Z",
    "acceptance_count": 13,
    "prayer_log_count": 46,
    "is_expired": false,
    "has_accepted": true,
    "has_prayed_today": true,
    "is_owner": false
  },
  "user": 8,
  "user_email": "mike@example.com",
  "prayed_on_date": "2025-11-18",
  "created_at": "2025-11-18T18:30:00Z"
}
```

**Side Effects:**
- Increments `prayer_log_count` for the request
- Checks for `faithful_intercessor` milestone (7 consecutive days)
- Daily notification sent to requester at 11:30 PM with prayer count

**Error:** `400 Bad Request`

```json
{
  "detail": "You must accept this prayer request before marking it as prayed."
}
```

```json
{
  "detail": "You have already marked this prayer for today."
}
```

---

## 9. Send Thank You Message

Send a thank you message to all who accepted your completed prayer request.

**Endpoint:** `POST /api/prayer-requests/{id}/send-thanks/`

**Permissions:**
- Must be the requester
- Status must be `completed`

**Request Body:**

```json
{
  "message": "Thank you all for your faithful prayers! My father's surgery went well and he is recovering. God is good!"
}
```

**Field Constraints:**
- `message`: Required, max 500 characters, cannot be empty

**Response:** `200 OK`

```json
{
  "detail": "Thank you message sent to 13 people.",
  "recipient_count": 13
}
```

**Side Effects:**
- Creates `PRAYER_REQUEST_THANKS_SENT` event
- Creates activity feed item for each user who accepted the request
- Activity feed items contain the thank you message

**Error:** `403 Forbidden`

```json
{
  "detail": "Only the requester can send a thank you message."
}
```

**Error:** `400 Bad Request`

```json
{
  "detail": "Thank you messages can only be sent for completed prayer requests."
}
```

```json
{
  "detail": "No one accepted this prayer request."
}
```

```json
{
  "message": [
    "Message cannot be empty."
  ]
}
```

---

## Data Models

### PrayerRequest

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `title` | string | Prayer request title (max 200 chars) |
| `description` | text | Detailed description |
| `is_anonymous` | boolean | Hide requester from users (visible to admins) |
| `duration_days` | integer | Duration in days (1-7) |
| `expiration_date` | datetime | Auto-calculated expiration timestamp |
| `image` | file/url | Optional image |
| `thumbnail_url` | url | Cached thumbnail URL |
| `reviewed` | boolean | Whether moderation is complete |
| `status` | string | `pending_moderation`, `approved`, `rejected`, `completed`, `deleted` |
| `requester` | object | User who submitted (null if anonymous) |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |
| `acceptance_count` | integer | Number of users who accepted |
| `prayer_log_count` | integer | Total prayer logs |
| `is_expired` | boolean | Whether request has expired |
| `has_accepted` | boolean | Current user has accepted |
| `has_prayed_today` | boolean | Current user prayed today |
| `is_owner` | boolean | Current user is the requester/owner |

### Status Flow

```
pending_moderation → approved → completed
                   ↓
                rejected
                   ↓
                deleted (soft delete)
```

---

## Milestones

The following milestones are automatically awarded:

| Milestone | Trigger | Description |
|-----------|---------|-------------|
| `first_prayer_request_created` | Create 1st approved request | "Shared your first prayer request" |
| `first_prayer_request_accepted` | Accept 1st request from someone else | "Accepted your first prayer request" (excludes own requests) |
| `prayer_warrior_10` | Accept 10 requests from others | "Prayer Warrior - 10 Requests Accepted" (excludes own requests) |
| `prayer_warrior_50` | Accept 50 requests from others | "Prayer Warrior Champion - 50 Requests Accepted" (excludes own requests) |
| `faithful_intercessor` | Pray 7 consecutive days | "Faithful Intercessor - 7 Consecutive Days" |

---

## Scheduled Tasks

### Check Expired Prayer Requests
**Schedule:** Daily at 11:00 PM
**Task:** `prayers.tasks.check_expired_prayer_requests_task`

- Finds approved requests where `expiration_date <= now()`
- Sets status to `completed`
- Creates `PRAYER_REQUEST_COMPLETED` event
- Creates activity feed item for requester

### Send Daily Prayer Count Notifications
**Schedule:** Daily at 11:30 PM
**Task:** `prayers.tasks.send_daily_prayer_count_notifications_task`

- Counts unique users who prayed for each active request today
- Creates activity feed item: "X users prayed for your request today"
- Only sent if count > 0

---

## Content Moderation

All prayer requests undergo two-tier moderation:

### 1. Profanity Filter
- Uses `better-profanity` library
- Checks title and description
- Immediate rejection if profanity detected
- Email sent to admin with details

### 2. AI Moderation (Claude Sonnet 4.5)
- Evaluates for:
  - Genuine prayer need
  - Appropriate content for Christian community
  - Not spam or promotional
  - Coherent and clear
  - Safe and non-harmful
- Returns JSON with approval decision and reasons
- Email sent to admin if rejected or error occurs

### Moderation Email
**Recipient:** fastandprayhelp@gmail.com
**Subject:** Prayer Request Rejected/Error - Manual Review Needed
**Contents:**
- Prayer request ID and details
- Requester information
- Moderation results
- Link to admin panel

---

## Activity Feed Integration

Prayer requests create the following activity feed items:

| Event Type | Recipient | Trigger | Title |
|------------|-----------|---------|-------|
| `prayer_request_accepted` | Requester | User accepts | "John accepted your prayer request" |
| `prayer_request_completed` | Requester | Expiration | "Your prayer request has completed" |
| `prayer_request_daily_count` | Requester | Daily at 11:30 PM | "5 people prayed for you today" |
| `prayer_request_thanks` | Acceptors | Send thanks | Thank you message from requester |

---

## Admin Interface

Access at `/admin/prayers/prayerrequest/`

### Features
- List display with status, reviewed flag, anonymous indicator
- Filters: status, reviewed, is_anonymous, duration, created_at
- Search by title, description, requester email/name
- Formatted JSON display for moderation results
- Image preview for requests with images
- Statistics: acceptance count, prayer log count

### Bulk Actions
- **Approve selected prayer requests**: Set status to `approved`, mark as reviewed
- **Reject selected prayer requests**: Set status to `rejected`, mark as reviewed

### Moderation Result Display
Shows formatted JSON with:
- Profanity check results
- LLM moderation decision
- Concerns identified
- Rejection reason

---

## Error Handling

### Common HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| `200` | Success | Request processed |
| `201` | Created | Resource created successfully |
| `204` | No Content | Successful deletion |
| `400` | Bad Request | Validation error, duplicate action |
| `401` | Unauthorized | Missing/invalid authentication |
| `403` | Forbidden | Permission denied |
| `404` | Not Found | Resource doesn't exist |
| `500` | Server Error | Internal error (contact admin) |

### Validation Errors

All validation errors return `400 Bad Request` with details:

```json
{
  "field_name": [
    "Error message for this field"
  ],
  "non_field_errors": [
    "General validation errors"
  ]
}
```

---

## Usage Examples

### Example 1: Create and Track a Prayer Request

```bash
# 1. Create prayer request
curl -X POST https://api.example.com/api/prayer-requests/ \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Pray for job interview",
    "description": "I have an important job interview tomorrow. Please pray for confidence and clarity.",
    "is_anonymous": false,
    "duration_days": 3
  }'

# 2. Accept someone's prayer request
curl -X POST https://api.example.com/api/prayer-requests/5/accept/ \
  -H "Authorization: Bearer YOUR_TOKEN"

# 3. Mark that you prayed today
curl -X POST https://api.example.com/api/prayer-requests/5/mark-prayed/ \
  -H "Authorization: Bearer YOUR_TOKEN"

# 4. Send thanks after completion
curl -X POST https://api.example.com/api/prayer-requests/5/send-thanks/ \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Thank you everyone! The interview went great and I got the job!"
  }'
```

### Example 2: Get User's Prayer Commitments

```bash
# Get all requests I've accepted
curl -X GET https://api.example.com/api/prayer-requests/accepted/ \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Example 3: Update Pending Request

```bash
# Update request before it's moderated
curl -X PATCH https://api.example.com/api/prayer-requests/12/ \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Updated with more specific details about my situation."
  }'
```

### Example 4: Filter Prayer Requests by Status

```bash
# Get all completed prayer requests
curl -X GET https://api.example.com/api/prayer-requests/?status=completed \
  -H "Authorization: Bearer YOUR_TOKEN"

# Get pending moderation requests
curl -X GET https://api.example.com/api/prayer-requests/?status=pending_moderation \
  -H "Authorization: Bearer YOUR_TOKEN"

# Get both completed and pending moderation requests
curl -X GET https://api.example.com/api/prayer-requests/?status=completed,pending_moderation \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Best Practices

1. **Check `has_accepted` before showing accept button**
   - Prevents duplicate acceptance errors
   - Improves UX
   - Note: User's own requests will have `has_accepted: true` after approval

2. **Check `has_prayed_today` before allowing prayer logging**
   - Users can only log once per day
   - Show checkmark if already prayed

3. **Display `is_expired` status to users**
   - Show expired requests differently
   - Prevent actions on expired requests

4. **Handle moderation delays gracefully**
   - Show "pending moderation" state
   - Allow edits while pending
   - Notify user when approved/rejected

5. **Respect anonymous requests**
   - Don't display requester info when `is_anonymous: true`
   - Still show requester to admins

6. **Use thumbnail URLs when available**
   - Cached thumbnails load faster
   - Fallback to full image URL if needed

7. **Implement pagination**
   - Use Django REST Framework pagination
   - Don't load all requests at once

8. **Use `is_owner` to show owner-specific features**
   - Check `is_owner` to determine if user created the request
   - Show edit/delete buttons only when `is_owner: true`
   - Display "Your Request" badge or special styling for owner
   - Allow marking own requests as prayed
   - User's own requests automatically appear in `/accepted/` list

---

## Support

For issues or questions:
- **Technical Issues:** Check Django admin for moderation details
- **Moderation Concerns:** Email to fastandprayhelp@gmail.com
- **API Errors:** Check logs in Sentry dashboard
- **Feature Requests:** Submit via GitHub issues

---

## Changelog

### v1.0.0 (2025-11-18)
- Initial release
- Complete prayer requests CRUD API
- Automated two-tier content moderation
- Milestone system with 5 achievements
- Daily scheduled tasks for expiration and notifications
- Activity feed integration
- Admin interface with bulk actions
