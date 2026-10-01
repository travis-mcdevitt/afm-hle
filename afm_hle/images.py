"""Lossless encoding conversion for static HLE images; no resize or editing."""
import base64
import io

POLICY = 'verified-inline-png-jpeg;static-webp-gif-to-rgba-png-v2'


def gateway_image(value):
    if not isinstance(value,str): raise ValueError('image must be an inline data URL')
    if not value.startswith(('data:image/png;base64,','data:image/jpeg;base64,',
                             'data:image/webp;base64,','data:image/gif;base64,')):
        raise ValueError('unsupported inline image encoding')
    from PIL import Image
    with Image.open(io.BytesIO(base64.b64decode(value.split(',',1)[1],validate=True))) as image:
        if getattr(image,'n_frames',1) != 1:
            raise ValueError('animated image requires separate protocol review')
        if image.format in ('PNG','JPEG'):
            # Keep encoded bytes unchanged; correct only a mislabeled MIME type.
            mime = 'png' if image.format == 'PNG' else 'jpeg'
            return 'data:image/'+mime+';base64,'+value.split(',',1)[1]
        if image.format not in ('WEBP','GIF'):
            raise ValueError('unsupported image bytes')
        out=io.BytesIO()
        image.convert('RGBA').save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()
