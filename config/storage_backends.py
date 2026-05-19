"""
Custom S3 storage backends for ChemicAlly.

Uses separate backends for static and media files to apply different
cache policies, access controls, and URL signing behavior.

- StaticStorage:  public-read for CSS/JS/images collected via collectstatic.
                  Uses a long cache TTL and no querystring authentication.
- MediaStorage:   private for user-uploaded files.  Served through
                  signed (querystring-authenticated) URLs that expire.
"""

from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage


class StaticStorage(S3Boto3Storage):
    """Storage backend for collectstatic assets.

    Files are stored under ``static/`` in the bucket. A generous cache
    TTL is applied because these assets change infrequently (on deploy).
    """

    location = "static"
    file_overwrite = True
    querystring_auth = False

    custom_domain = getattr(settings, "AWS_S3_CUSTOM_DOMAIN", None)

    default_acl = None

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("object_parameters", {"CacheControl": "max-age=31536000"})
        super().__init__(*args, **kwargs)


class MediaStorage(S3Boto3Storage):
    """Storage backend for user-uploaded media files.

    Files are stored under ``media/`` in the bucket. Access is granted
    via temporary signed URLs (querystring authentication) that expire
    after one hour.
    """

    location = "media"
    file_overwrite = False
    querystring_auth = True
    querystring_expire = 3600  # 1 hour
    custom_domain = None  # always use signed URLs, never a public CDN

    default_acl = None

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("object_parameters", {"CacheControl": "max-age=86400"})
        super().__init__(*args, **kwargs)
