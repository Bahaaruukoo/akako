from django.conf import settings
from django.core.files.storage import storages
from storages.backends.s3 import S3Storage


class ObjectStorage(S3Storage):
    bucket_name = settings.OBJECT_STORAGE_BUCKET_NAME
    region_name = settings.OBJECT_STORAGE_REGION
    endpoint_url = settings.OBJECT_STORAGE_ENDPOINT_URL
    access_key = settings.OBJECT_STORAGE_ACCESS_KEY_ID
    secret_key = settings.OBJECT_STORAGE_SECRET_ACCESS_KEY
    file_overwrite = False


class PublicMediaStorage(ObjectStorage):
    location = "public"
    default_acl = "public-read"
    querystring_auth = False
    custom_domain = settings.OBJECT_STORAGE_CDN_DOMAIN
    object_parameters = {"CacheControl": "public, max-age=86400"}


class PrivateDocumentStorage(ObjectStorage):
    location = "private"
    default_acl = "private"
    querystring_auth = True
    querystring_expire = 300
    custom_domain = None


def public_media_storage():
    return storages["public_media"]


def private_document_storage():
    return storages["private_documents"]
