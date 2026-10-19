# -*- coding: utf-8 -*-
"""Reading the us/ shelf that equity/us_capture.py writes.

us_capture.py banks every file it fetches under ``us/{day}/{source}/{file}``
and keeps one pointer per (source, file) at ``us/latest/{source}__{file}.json``
with the object key, SHA-256 and capture time. The loaders for the SEC's
structured data sets (statements, insider transactions, 13F holdings) all
read the shelf the same way: list the pointers for one source, pick the
files to load, fetch each by its key. This module is that reading, once,
for the laptop copy (LocalSource) and the bucket (S3Source).

Python 3.9; stdlib, boto3 lazily in S3 mode.
"""
import glob
import json
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_ROOT = os.environ.get("US_ARCHIVE_ROOT",
                            os.path.join(HERE, "..", "..", "equity", "data", "raw", "us"))
S3_PREFIX = "us"

_Q = re.compile(r"(\d{4})q([1-4])", re.I)


def quarter_of(filename):
    """'2026q3_form345.zip' or 'fsds_2026q3.zip' -> '2026q3'; None if no quarter."""
    m = _Q.search(filename or "")
    return ("%sq%s" % (m.group(1), m.group(2))).lower() if m else None


def quarter_key(q):
    return (int(q[:4]), int(q[5]))


class LocalSource(object):
    """The laptop copy of the us/ shelf (us_capture.py's LocalStore layout)."""

    name = "local"

    def __init__(self, source, root=LOCAL_ROOT):
        self.source = source
        self.root = os.path.abspath(root)

    def pointers(self):
        """{filename: pointer record} for every good file of this source."""
        out = {}
        for p in glob.glob(os.path.join(self.root, "latest", self.source + "__*.json")):
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("status", "ok") == "ok" and rec.get("file"):
                out[rec["file"]] = rec
        return out

    def fetch(self, rec, dest):
        key = rec["key"]
        rel = key[len(S3_PREFIX) + 1:] if key.startswith(S3_PREFIX + "/") else key
        shutil.copyfile(os.path.join(self.root, rel), dest)


class S3Source(object):
    """The bucket (EDINET_S3_* credentials, the same bucket the EDINET
    archive lives in), prefix us/."""

    name = "s3"

    def __init__(self, source):
        import boto3
        self.source = source
        self.bucket = os.environ["EDINET_S3_BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ["EDINET_S3_ENDPOINT"],
            aws_access_key_id=os.environ["EDINET_S3_KEY_ID"],
            aws_secret_access_key=os.environ["EDINET_S3_SECRET"],
            region_name=os.environ.get("EDINET_S3_REGION", "auto"))

    def pointers(self):
        prefix = S3_PREFIX + "/latest/" + self.source + "__"
        out = {}
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                body = self.client.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
                rec = json.loads(body.decode("utf-8"))
                if rec.get("status", "ok") == "ok" and rec.get("file"):
                    out[rec["file"]] = rec
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return out

    def fetch(self, rec, dest):
        self.client.download_file(self.bucket, rec["key"], dest)


def make_source(kind, source, root=None):
    if kind == "s3":
        return S3Source(source)
    return LocalSource(source, root or LOCAL_ROOT)
