# Promo Email Images Feature

## Overview
This feature allows administrators to easily upload images and generate URLs for use in promotional emails, eliminating the need to host images externally.

## How It Works

### 1. Uploading Images
1. Go to Django Admin → Notifications → Promo Email Images
2. Click "Add Promo Email Image"
3. Fill in:
   - **Name**: Descriptive name for the image (e.g., "Easter 2024 Banner")
   - **Image**: Upload your image file
   - **Description**: Optional description of the image content
4. Click "Save"

### 2. Getting Image URLs
After uploading, you can copy the image URL in two ways:

**Method 1: From the Image List**
- In the Promo Email Images list view, click on the "Image URL" field 
- The URL will be automatically copied to your clipboard

**Method 2: From the Promo Email Form**
- When creating/editing a promotional email, scroll to the "Content HTML" field
- Available images are displayed below the field with:
  - Image preview thumbnail
  - Clickable URL ready to copy

### 3. Using Images in Emails
Copy the image URL and use it in your HTML content:

```html
<img src="https://yourdomain.com/media/promo_email_images/banner.jpg" alt="Event Banner" />
```

## Technical Details

### Storage Configuration
- **Development**: Images stored locally in `/media/promo_email_images/`
- **Production**: Images automatically uploaded to AWS S3
- URLs are automatically generated based on the environment

### Model Structure
```python
class PromoEmailImage(models.Model):
    name = models.CharField(max_length=200)
    image = models.ImageField(upload_to='promo_email_images/')
    description = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(User, ...)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### Admin Features
- **Image Preview**: Thumbnails in the admin list view
- **Auto-attribution**: Automatically tracks who uploaded each image
- **Click-to-copy URLs**: Easy URL copying with visual feedback
- **Integration**: Available images shown directly in the promo email form

## Security & Performance
- Images are validated as proper image files
- Automatic file naming prevents conflicts
- S3 integration provides CDN benefits in production
- Media URLs properly secured with Django's built-in protections

## Future Enhancements
Potential improvements could include:
- Image resizing/optimization
- Bulk upload capability
- Image categorization/tagging
- Usage tracking (which emails use which images)
- Image gallery view with better organization

## Migration Applied
The feature is ready to use after running:
```bash
python manage.py migrate notifications
```

## Example Usage Flow
1. Upload logo.png with name "Company Logo"
2. Copy the generated URL from admin
3. Use in promo email: `<img src="[copied-url]" alt="Company Logo" />`
4. Send email with embedded image that recipients can view directly