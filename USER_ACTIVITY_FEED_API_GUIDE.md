# User Activity Feed API Guide
## React Native TanStack Query Integration

This document provides a comprehensive guide to the User Activity Feed system and its API endpoints for use with React Native and TanStack Query.

---

## 🚀 System Overview

The User Activity Feed system provides a unified way for users to view all their relevant activities in one place, with read/unread tracking. This includes events like joining/leaving fasts, fast reminders, devotional availability, individual user milestones, fast participation milestones, and content publications.

### Key Features
- **📱 Unified Activity Feed**: Single endpoint for all user-relevant activities
- **👁️ Read/Unread Tracking**: Track what users have and haven't seen
- **🎯 Activity Types**: Event-based, reminder-based, and system-generated activities
- **🔍 Filtering & Search**: Filter by activity type, read status, and date ranges
- **⚡ Real-time Updates**: Automatic creation from user actions and system events
- **🖼️ Target Thumbnails**: Automatic inclusion of thumbnail URLs from related objects (Fasts, Profiles, etc.)

---

## 📊 Activity Types

The system tracks the following activity types:

| Activity Type | Display Name | Description |
|---------------|-------------|-------------|
| `event` | Event | Generic event activity |
| `fast_start` | Fast Started | A fast has begun |
| `fast_join` | Joined Fast | User joined a fast |
| `fast_leave` | Left Fast | User left a fast |
| `devotional_available` | Devotional Available | New devotional is available |
| `milestone` | Milestone Reached | User milestone achieved (e.g., first fast joined, first fast completed) |
| `fast_reminder` | Fast Reminder | Reminder about an active fast |
| `devotional_reminder` | Devotional Reminder | Reminder about available devotional |
| `user_account_created` | User Account Created | New user account was created |
| `article_published` | Article Published | New article was published |
| `recipe_published` | Recipe Published | New recipe was published |
| `video_published` | Video Published | New video was published |

---

## 🔗 API Endpoints

All endpoints are prefixed with `/api/events/` and require authentication.

### Base URL
```
https://your-domain.com/api/events/
```

### Authentication
All endpoints require a valid JWT token in the Authorization header:
```
Authorization: Bearer <your-jwt-token>
```

---

## 📋 API Reference

### 1. Get User Activity Feed

**Endpoint:** `GET /api/events/activity-feed/`

**Description:** Retrieve the user's activity feed with filtering and pagination support.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `activity_type` | string | No | Filter by activity type (see Activity Types table) |
| `is_read` | boolean | No | Filter by read status (`true`/`false`) |
| `start_date` | string | No | ISO format date (e.g., `2024-01-01T00:00:00Z`) |
| `end_date` | string | No | ISO format date |
| `page` | integer | No | Page number for pagination (default: 1) |
| `page_size` | integer | No | Items per page (default: 20) |

**Example Request:**
```javascript
// TanStack Query example
const { data, isLoading, error } = useQuery({
  queryKey: ['activity-feed', { page: 1, is_read: false }],
  queryFn: async () => {
    const response = await fetch('/api/events/activity-feed/?page=1&is_read=false', {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    });
    return response.json();
  },
});
```

**Response:**
```json
{
  "count": 25,
  "next": "https://api.example.com/events/activity-feed/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "activity_type": "fast_join",
      "activity_type_display": "Joined Fast",
      "title": "Joined the Great Lent Fast",
      "description": "You joined the Great Lent Fast and can now participate in daily devotionals and community activities.",
      "is_read": false,
      "read_at": null,
      "created_at": "2024-01-15T10:30:00Z",
      "age_display": "2h ago",
      "data": {
        "fast_id": 123,
        "fast_name": "Great Lent Fast",
        "participant_count": 45
      },
      "target_type": "hub.fast",
      "target_id": 123,
      "target_thumbnail": "https://example.com/media/fast_images/thumbnails/great_lent_thumb.jpg"
    },
    {
      "id": 2,
      "activity_type": "devotional_reminder",
      "activity_type_display": "Devotional Reminder",
      "title": "New devotional available",
      "description": "Today's devotional 'Finding Peace in Prayer' is now available.",
      "is_read": true,
      "read_at": "2024-01-14T08:15:00Z",
      "created_at": "2024-01-14T08:00:00Z",
      "age_display": "1d ago",
      "data": {
        "devotional_id": 456,
        "devotional_title": "Finding Peace in Prayer",
        "fast_id": 123
      },
      "target_type": "hub.devotional",
      "target_id": 456,
      "target_thumbnail": null
    },
    {
      "id": 3,
      "activity_type": "milestone",
      "activity_type_display": "Milestone Reached",
      "title": "Joined your first fast",
      "description": "Congratulations on joining your first fast!",
      "is_read": false,
      "read_at": null,
      "created_at": "2024-01-15T10:30:00Z",
      "age_display": "2h ago",
      "data": {
        "milestone_type": "first_fast_join",
        "milestone_id": 789,
        "fast_id": 123,
        "fast_name": "Great Lent Fast"
      },
      "target_type": "hub.fast",
      "target_id": 123,
      "target_thumbnail": "https://example.com/media/fast_images/thumbnails/great_lent_thumb.jpg"
    }
  ]
}
```

---

### 2. Get Activity Feed Summary

**Endpoint:** `GET /api/events/activity-feed/summary/`

**Description:** Get summary statistics for the user's activity feed.

**Example Request:**
```javascript
const { data } = useQuery({
  queryKey: ['activity-feed-summary'],
  queryFn: async () => {
    const response = await fetch('/api/events/activity-feed/summary/', {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    });
    return response.json();
  },
});
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
    "milestone": 1,
    "article_published": 3,
    "video_published": 2
  },
  "recent_activity": [
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
    // ... up to 5 recent items
  ]
}
```

---

### 3. Mark Activities as Read

**Endpoint:** `POST /api/events/activity-feed/mark-read/`

**Description:** Mark specific activity items or all items as read.

**Request Body Options:**

**Option 1: Mark specific items**
```json
{
  "activity_ids": [1, 2, 3, 4, 5]
}
```

**Option 2: Mark all unread items**
```json
{
  "mark_all": true
}
```

**Example Request:**
```javascript
const markAsReadMutation = useMutation({
  mutationFn: async ({ activityIds, markAll }) => {
    const body = markAll 
      ? { mark_all: true }
      : { activity_ids: activityIds };
    
    const response = await fetch('/api/events/activity-feed/mark-read/', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    return response.json();
  },
  onSuccess: () => {
    // Invalidate queries to refresh the feed
    queryClient.invalidateQueries(['activity-feed']);
    queryClient.invalidateQueries(['activity-feed-summary']);
  },
});

// Usage
markAsReadMutation.mutate({ activityIds: [1, 2, 3] });
// or
markAsReadMutation.mutate({ markAll: true });
```

**Response:**
```json
{
  "message": "Marked 5 items as read",
  "updated_count": 5
}
```

---

### 4. Generate Activity Feed (Admin Only)

**Endpoint:** `POST /api/events/activity-feed/generate/`

**Description:** Generate activity feed items for a user (admin/development use only).

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

---

## 🔧 TanStack Query Integration Examples

### Complete React Native Hook

```javascript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

// Custom hook for activity feed
export const useActivityFeed = (filters = {}) => {
  return useQuery({
    queryKey: ['activity-feed', filters],
    queryFn: async () => {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          params.append(key, value);
        }
      });
      
      const response = await fetch(`/api/events/activity-feed/?${params}`, {
        headers: {
          'Authorization': `Bearer ${getToken()}`,
        },
      });
      
      if (!response.ok) {
        throw new Error('Failed to fetch activity feed');
      }
      
      return response.json();
    },
    staleTime: 1000 * 60 * 2, // 2 minutes
    cacheTime: 1000 * 60 * 10, // 10 minutes
  });
};

// Custom hook for activity feed summary
export const useActivityFeedSummary = () => {
  return useQuery({
    queryKey: ['activity-feed-summary'],
    queryFn: async () => {
      const response = await fetch('/api/events/activity-feed/summary/', {
        headers: {
          'Authorization': `Bearer ${getToken()}`,
        },
      });
      
      if (!response.ok) {
        throw new Error('Failed to fetch activity feed summary');
      }
      
      return response.json();
    },
    staleTime: 1000 * 60 * 5, // 5 minutes
    cacheTime: 1000 * 60 * 15, // 15 minutes
  });
};

// Custom hook for marking activities as read
export const useMarkActivityRead = () => {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: async ({ activityIds, markAll }) => {
      const body = markAll 
        ? { mark_all: true }
        : { activity_ids: activityIds };
      
      const response = await fetch('/api/events/activity-feed/mark-read/', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${getToken()}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      });
      
      if (!response.ok) {
        throw new Error('Failed to mark activities as read');
      }
      
      return response.json();
    },
    onSuccess: () => {
      // Invalidate and refetch activity feed queries
      queryClient.invalidateQueries(['activity-feed']);
      queryClient.invalidateQueries(['activity-feed-summary']);
    },
  });
};
```

### React Native Component Example

```jsx
import React, { useState } from 'react';
import { View, Text, FlatList, TouchableOpacity, RefreshControl } from 'react-native';
import { useActivityFeed, useActivityFeedSummary, useMarkActivityRead } from './hooks';

const ActivityFeedScreen = () => {
  const [filters, setFilters] = useState({ page: 1 });
  const [refreshing, setRefreshing] = useState(false);
  
  const { 
    data: feedData, 
    isLoading, 
    error, 
    refetch 
  } = useActivityFeed(filters);
  
  const { data: summaryData } = useActivityFeedSummary();
  const markAsReadMutation = useMarkActivityRead();
  
  const handleRefresh = async () => {
    setRefreshing(true);
    await refetch();
    setRefreshing(false);
  };
  
  const handleMarkAsRead = (activityIds) => {
    markAsReadMutation.mutate({ activityIds });
  };
  
  const handleMarkAllAsRead = () => {
    markAsReadMutation.mutate({ markAll: true });
  };
  
  const renderActivityItem = ({ item }) => (
    <TouchableOpacity 
      style={[
        styles.activityItem,
        !item.is_read && styles.unreadItem
      ]}
      onPress={() => handleMarkAsRead([item.id])}
    >
      <Text style={styles.activityTitle}>{item.title}</Text>
      <Text style={styles.activityDescription}>{item.description}</Text>
      <Text style={styles.activityAge}>{item.age_display}</Text>
      {!item.is_read && <View style={styles.unreadIndicator} />}
    </TouchableOpacity>
  );
  
  if (isLoading) {
    return <Text>Loading...</Text>;
  }
  
  if (error) {
    return <Text>Error: {error.message}</Text>;
  }
  
  return (
    <View style={styles.container}>
      {/* Summary Header */}
      {summaryData && (
        <View style={styles.summaryHeader}>
          <Text>Total: {summaryData.total_items}</Text>
          <Text>Unread: {summaryData.unread_count}</Text>
          {summaryData.unread_count > 0 && (
            <TouchableOpacity onPress={handleMarkAllAsRead}>
              <Text style={styles.markAllButton}>Mark All Read</Text>
            </TouchableOpacity>
          )}
        </View>
      )}
      
      {/* Activity Feed List */}
      <FlatList
        data={feedData?.results || []}
        renderItem={renderActivityItem}
        keyExtractor={(item) => item.id.toString()}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />
        }
        onEndReached={() => {
          if (feedData?.next) {
            setFilters(prev => ({ ...prev, page: prev.page + 1 }));
          }
        }}
      />
    </View>
  );
};
```

---

## 🎯 Best Practices

### 1. **Caching Strategy**
```javascript
// Use appropriate stale times for different data types
const queryConfig = {
  // Activity feed - shorter stale time for real-time feel
  'activity-feed': { staleTime: 1000 * 60 * 2 }, // 2 minutes
  
  // Summary - can be slightly stale
  'activity-feed-summary': { staleTime: 1000 * 60 * 5 }, // 5 minutes
};
```

### 2. **Optimistic Updates**
```javascript
const markAsReadMutation = useMutation({
  mutationFn: markActivitiesAsRead,
  onMutate: async ({ activityIds }) => {
    // Cancel outgoing refetches
    await queryClient.cancelQueries(['activity-feed']);
    
    // Snapshot previous value
    const previousFeed = queryClient.getQueryData(['activity-feed']);
    
    // Optimistically update
    queryClient.setQueryData(['activity-feed'], (old) => ({
      ...old,
      results: old.results.map(item => 
        activityIds.includes(item.id) 
          ? { ...item, is_read: true, read_at: new Date().toISOString() }
          : item
      )
    }));
    
    return { previousFeed };
  },
  onError: (err, variables, context) => {
    // Rollback on error
    if (context?.previousFeed) {
      queryClient.setQueryData(['activity-feed'], context.previousFeed);
    }
  },
});
```

### 3. **Infinite Scroll**
```javascript
const useInfiniteActivityFeed = (filters = {}) => {
  return useInfiniteQuery({
    queryKey: ['activity-feed-infinite', filters],
    queryFn: async ({ pageParam = 1 }) => {
      const params = new URLSearchParams({ 
        ...filters, 
        page: pageParam 
      });
      
      const response = await fetch(`/api/events/activity-feed/?${params}`);
      return response.json();
    },
    getNextPageParam: (lastPage) => {
      return lastPage.next ? lastPage.next.split('page=')[1] : undefined;
    },
  });
};
```

### 4. **Real-time Updates**
```javascript
// Poll for updates when app comes to foreground
const { data } = useActivityFeed(filters, {
  refetchOnWindowFocus: true,
  refetchInterval: 1000 * 60 * 5, // Poll every 5 minutes
});
```

---

## 📱 UI/UX Recommendations

### Activity Item Design
- **Unread Indicator**: Use a colored dot or different background for unread items
- **Age Display**: Show relative time (e.g., "2h ago", "1d ago")
- **Activity Icons**: Use different icons for different activity types
- **Swipe Actions**: Allow swipe-to-mark-read functionality

### Performance Optimizations
- **Pagination**: Load 20-50 items per page
- **Virtual Lists**: Use FlatList with `getItemLayout` for better performance
- **Image Lazy Loading**: If activities include images/thumbnails
- **Background Sync**: Sync when app becomes active

### Accessibility
- **Screen Reader Support**: Provide meaningful content descriptions
- **High Contrast**: Ensure good contrast for read/unread states
- **Touch Targets**: Minimum 44pt touch targets for interactive elements

---

## 🔧 Error Handling

### Common Error Scenarios

```javascript
const { data, error, isError } = useActivityFeed();

if (isError) {
  // Handle different error types
  switch (error.status) {
    case 401:
      // Redirect to login
      break;
    case 403:
      // Show permission denied message
      break;
    case 500:
      // Show generic error message
      break;
    default:
      // Show network error message
      break;
  }
}
```

### Retry Logic

```javascript
const { data } = useActivityFeed(filters, {
  retry: (failureCount, error) => {
    // Don't retry on 4xx errors
    if (error.status >= 400 && error.status < 500) {
      return false;
    }
    // Retry up to 3 times for other errors
    return failureCount < 3;
  },
  retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000),
});
```

---

## 📊 Data Structure Reference

### Activity Feed Item
```typescript
interface ActivityFeedItem {
  id: number;
  activity_type: string;
  activity_type_display: string;
  title: string;
  description: string;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
  age_display: string;
  data: Record<string, any>;
  target_type: string | null;
  target_id: number | null;
  target_thumbnail: string | null;
}
```

### Feed Response
```typescript
interface ActivityFeedResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: ActivityFeedItem[];
}
```

### Summary Response
```typescript
interface ActivityFeedSummary {
  total_items: number;
  unread_count: number;
  read_count: number;
  activity_types: Record<string, number>;
  recent_activity: ActivityFeedItem[];
}
```

---

## 🚀 Getting Started Checklist

1. **✅ Set up TanStack Query** in your React Native app
2. **✅ Configure authentication** with JWT tokens
3. **✅ Implement the custom hooks** provided above
4. **✅ Create your activity feed UI** components
5. **✅ Add error handling** and loading states
6. **✅ Implement mark-as-read** functionality
7. **✅ Add pull-to-refresh** and pagination
8. **✅ Test with different activity types**
9. **✅ Optimize for performance** with proper caching
10. **✅ Add accessibility** features

---

This guide provides everything you need to integrate the User Activity Feed system into your React Native app using TanStack Query. The system is designed to be scalable, performant, and user-friendly.

