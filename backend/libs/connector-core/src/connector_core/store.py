"""Raw snapshots in the versioned S3 bucket.

One key per object, overwritten on change. Versioning keeps the previous bytes.
A delete removes every version, then a head confirms the key is gone.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath

from botocore.exceptions import ClientError

from shared.telemetry import span

METADATA_HASH = "kbs-content-hash"
METADATA_VERSION = "kbs-version"
METADATA_ACL = "kbs-native-acl"
METADATA_SOURCE_UPDATED = "kbs-source-updated-at"
METADATA_SNAPSHOT_AT = "kbs-snapshot-at"
MISSING = frozenset({"404", "NoSuchKey", "NotFound"})


@dataclass(frozen=True)
class PutResult:
    skipped: bool
    version_id: str | None


def object_key(source_id: str, object_type: str, object_id: str, ext: str) -> str:
    return f"raw/{source_id}/{object_type}/{object_id}.{ext}"


class SnapshotStore:
    def __init__(self, client: object, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def put(
        self,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> PutResult:
        """Write the object unless the stored content hash already matches."""
        current = self._head(key)
        if current is not None:
            stored = current.get("Metadata", {}).get(METADATA_HASH)
            if stored == metadata[METADATA_HASH]:
                return PutResult(skipped=True, version_id=current.get("VersionId"))
        source_id = key.split("/")[1] if key.startswith("raw/") else ""
        with span("s3.put", source_id=source_id, bytes=len(body)):
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                Metadata=metadata,
            )
        return PutResult(skipped=False, version_id=response.get("VersionId"))

    def delete_all_versions(self, key: str) -> int:
        """Remove the key and every version. Falls back to one delete if versioning is absent."""
        batch: list[dict[str, str]] = []
        try:
            pages = self.client.get_paginator("list_object_versions").paginate(
                Bucket=self.bucket, Prefix=key
            )
            for page in pages:
                for entry in page.get("Versions", []) + page.get("DeleteMarkers", []):
                    if entry["Key"] == key:
                        batch.append({"Key": key, "VersionId": entry["VersionId"]})
        except ClientError:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            return 1
        if not batch:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            return 1
        for start in range(0, len(batch), 1000):
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": batch[start : start + 1000], "Quiet": True},
            )
        return len(batch)

    def exists(self, key: str) -> bool:
        return self._head(key) is not None

    def list_object_ids(self, source_id: str) -> set[str]:
        prefix = f"raw/{source_id}/"
        found: set[str] = set()
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                found.add(PurePosixPath(item["Key"]).stem)
        return found

    def _head(self, key: str) -> dict | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in MISSING:
                return None
            raise
