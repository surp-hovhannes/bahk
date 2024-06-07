from PIL import Image
from io import BytesIO
from django.core.files.base import ContentFile

def resize_image(image, size=(300, 300)):
    img = Image.open(image)
    img = img.convert("RGB")
    img.thumbnail(size, Image.ANTIALIAS)
    
    img_io = BytesIO()
    img.save(img_io, format='JPEG')
    
    return ContentFile(img_io.getvalue(), f'{size[0]}x{size[1]}_{image.name}')
