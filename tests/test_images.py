import base64
import io
import unittest
from afm_hle.images import gateway_image

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, 'optional Pillow dependency unavailable')
class ImageTests(unittest.TestCase):
    def test_mislabeled_webp_preserves_pixels(self):
        src=io.BytesIO();Image.new('RGB',(8,8),(123,41,17)).save(src,format='WEBP')
        url='data:image/jpeg;base64,'+base64.b64encode(src.getvalue()).decode()
        converted=gateway_image(url)
        self.assertTrue(converted.startswith('data:image/png;base64,'))
        original=Image.open(io.BytesIO(src.getvalue())).convert('RGBA')
        actual=Image.open(io.BytesIO(base64.b64decode(converted.split(',')[1]))).convert('RGBA')
        self.assertEqual(actual.tobytes(),original.tobytes())

    def test_correct_jpeg_bytes_are_unchanged(self):
        src=io.BytesIO();Image.new('RGB',(8,8),'red').save(src,format='JPEG')
        url='data:image/jpeg;base64,'+base64.b64encode(src.getvalue()).decode()
        self.assertEqual(gateway_image(url),url)

    def test_static_conversion_preserves_pixels(self):
        for fmt in ('GIF','WEBP'):
            src=io.BytesIO();Image.new('RGBA',(8,8),(123,41,17,255)).save(src,format=fmt)
            url='data:image/'+fmt.lower()+';base64,'+base64.b64encode(src.getvalue()).decode()
            converted=gateway_image(url)
            original=Image.open(io.BytesIO(src.getvalue())).convert('RGBA')
            actual=Image.open(io.BytesIO(base64.b64decode(converted.split(',')[1]))).convert('RGBA')
            self.assertEqual(actual.tobytes(),original.tobytes())

    def test_animation_is_not_silently_flattened(self):
        src=io.BytesIO()
        Image.new('RGB',(4,4),'red').save(src,format='GIF',save_all=True,
            append_images=[Image.new('RGB',(4,4),'blue')],duration=100,loop=0)
        url='data:image/gif;base64,'+base64.b64encode(src.getvalue()).decode()
        with self.assertRaises(ValueError):gateway_image(url)
