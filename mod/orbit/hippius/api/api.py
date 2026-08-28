"""
Hippius API — BYOK (bring-your-own-key) proxy for Hippius decentralized S3 storage.

Credentials are NEVER stored server-side. Every request carries the caller's own
Hippius S3 credentials in headers, the API signs the S3 call with them, and the
keys are discarded when the request ends.

Headers (BYOK):
    x-hip-key:      Hippius S3 access key id        (required on authed routes)
    x-hip-secret:   Hippius S3 secret access key    (required on authed routes)
    x-hip-endpoint: S3 endpoint                     (default https://s3.hippius.com)
    x-hip-region:   S3 region                       (default "decentralized")

Get keys at https://console.hippius.com — Hippius speaks standard S3 (SigV4,
path-style), so the same keys work with aws-cli/boto3/rclone directly.
"""

import io
import json
import time
from typing import Optional

import boto3
import requests as _requests
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

DEFAULT_ENDPOINT = "https://s3.hippius.com"
DEFAULT_REGION = "decentralized"
ALLOWED_ENDPOINTS_NOTE = "any https Hippius S3 endpoint (s3.hippius.com, eu-central-1.hippius.com, us-east-1.hippius.com)"

app = FastAPI(
    title="Hippius BYOK API",
    description="Bring-your-own-key gateway to Hippius decentralized S3 storage.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── BYOK credential dependency ───────────────────────────────────────────

class Creds:
    def __init__(self, key: str, secret: str, endpoint: str, region: str):
        self.key = key
        self.secret = secret
        self.endpoint = endpoint
        self.region = region

    def client(self):
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            region_name=self.region,
            aws_access_key_id=self.key,
            aws_secret_access_key=self.secret,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=15,
                read_timeout=120,
                retries={"max_attempts": 2},
            ),
        )


def creds(
    x_hip_key: Optional[str] = Header(None),
    x_hip_secret: Optional[str] = Header(None),
    x_hip_endpoint: Optional[str] = Header(None),
    x_hip_region: Optional[str] = Header(None),
) -> Creds:
    if not x_hip_key or not x_hip_secret:
        raise HTTPException(
            401,
            detail={
                "error": "missing credentials",
                "how": "send x-hip-key and x-hip-secret headers (BYOK — get keys at https://console.hippius.com)",
            },
        )
    endpoint = (x_hip_endpoint or DEFAULT_ENDPOINT).strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise HTTPException(400, detail={"error": "endpoint must be https", "allowed": ALLOWED_ENDPOINTS_NOTE})
    return Creds(x_hip_key, x_hip_secret, endpoint, (x_hip_region or DEFAULT_REGION).strip())


def s3_error(e: Exception) -> HTTPException:
    if isinstance(e, ClientError):
        err = e.response.get("Error", {})
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 502)
        return HTTPException(
            status if 400 <= status < 600 else 502,
            detail={"error": err.get("Code", "S3Error"), "message": err.get("Message", str(e))},
        )
    if isinstance(e, EndpointConnectionError):
        return HTTPException(502, detail={"error": "EndpointUnreachable", "message": str(e)})
    return HTTPException(502, detail={"error": "S3Error", "message": str(e)})


# ── Info / health (no auth) ──────────────────────────────────────────────

@app.get("/")
def info():
    return {
        "name": "hippius",
        "version": "2.0.0",
        "description": "BYOK gateway to Hippius decentralized S3 storage — your keys, sent per-request, never stored.",
        "byok": {
            "get_keys": "https://console.hippius.com",
            "headers": ["x-hip-key", "x-hip-secret", "x-hip-endpoint (optional)", "x-hip-region (optional)"],
            "default_endpoint": DEFAULT_ENDPOINT,
            "default_region": DEFAULT_REGION,
        },
        "endpoints": {
            "GET /health": "liveness",
            "GET /status": "hippius network reachability",
            "POST /verify": "check credentials (lists buckets)",
            "GET /buckets": "list buckets",
            "POST /buckets": "create bucket {name, public?}",
            "DELETE /buckets/{bucket}": "delete empty bucket",
            "GET /buckets/{bucket}/objects": "list objects (?prefix=&token=&limit=)",
            "POST /buckets/{bucket}/objects": "upload multipart file (field: file, optional: key)",
            "GET /buckets/{bucket}/objects/download": "stream object (?key=)",
            "GET /buckets/{bucket}/objects/meta": "head object incl. metadata/CID (?key=)",
            "DELETE /buckets/{bucket}/objects": "delete object (?key=)",
            "GET /buckets/{bucket}/presign": "presigned URL (?key=&op=get|put&expires=)",
            "GET /buckets/{bucket}/policy": "bucket policy / public state",
            "PUT /buckets/{bucket}/policy": "set {public: bool}",
        },
        "docs": "https://docs.hippius.com/storage/s3/integration",
    }


@app.get("/health")
def health():
    return {"status": "ok", "ts": int(time.time())}


@app.get("/status")
def status():
    """Probe the Hippius network without credentials."""
    out = {"endpoint": DEFAULT_ENDPOINT, "region": DEFAULT_REGION, "ts": int(time.time())}
    try:
        t0 = time.time()
        r = _requests.get(DEFAULT_ENDPOINT, timeout=8)
        out["reachable"] = True
        out["latency_ms"] = int((time.time() - t0) * 1000)
        out["http"] = r.status_code
    except Exception as e:
        out["reachable"] = False
        out["error"] = str(e)
    return out


# ── Credentials ──────────────────────────────────────────────────────────

@app.post("/verify")
def verify(c: Creds = Depends(creds)):
    try:
        resp = c.client().list_buckets()
    except Exception as e:
        raise s3_error(e)
    buckets = [b["Name"] for b in resp.get("Buckets", [])]
    return {
        "valid": True,
        "endpoint": c.endpoint,
        "region": c.region,
        "owner": resp.get("Owner", {}).get("DisplayName") or resp.get("Owner", {}).get("ID"),
        "buckets": len(buckets),
        "bucket_names": buckets[:50],
    }


# ── Buckets ──────────────────────────────────────────────────────────────

class BucketCreate(BaseModel):
    name: str
    public: bool = False


def _public_policy(bucket: str) -> str:
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "PublicRead",
            "Effect": "Allow",
            "Principal": "*",
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{bucket}/*"],
        }],
    })


@app.get("/buckets")
def list_buckets(c: Creds = Depends(creds)):
    try:
        resp = c.client().list_buckets()
    except Exception as e:
        raise s3_error(e)
    return {
        "buckets": [
            {"name": b["Name"], "created": b.get("CreationDate").isoformat() if b.get("CreationDate") else None}
            for b in resp.get("Buckets", [])
        ]
    }


@app.post("/buckets")
def create_bucket(body: BucketCreate, c: Creds = Depends(creds)):
    s3 = c.client()
    try:
        s3.create_bucket(Bucket=body.name)
    except Exception as e:
        raise s3_error(e)
    made_public = False
    if body.public:
        try:
            s3.put_bucket_policy(Bucket=body.name, Policy=_public_policy(body.name))
            made_public = True
        except Exception:
            pass
    return {"created": True, "bucket": body.name, "public": made_public}


@app.delete("/buckets/{bucket}")
def delete_bucket(bucket: str, c: Creds = Depends(creds)):
    try:
        c.client().delete_bucket(Bucket=bucket)
    except Exception as e:
        raise s3_error(e)
    return {"deleted": True, "bucket": bucket}


# ── Objects ──────────────────────────────────────────────────────────────

@app.get("/buckets/{bucket}/objects")
def list_objects(
    bucket: str,
    prefix: str = Query(""),
    token: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    c: Creds = Depends(creds),
):
    kw = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": limit}
    if token:
        kw["ContinuationToken"] = token
    try:
        resp = c.client().list_objects_v2(**kw)
    except Exception as e:
        raise s3_error(e)
    return {
        "bucket": bucket,
        "objects": [
            {
                "key": o["Key"],
                "size": o.get("Size", 0),
                "modified": o.get("LastModified").isoformat() if o.get("LastModified") else None,
                "etag": (o.get("ETag") or "").strip('"'),
            }
            for o in resp.get("Contents", [])
        ],
        "truncated": resp.get("IsTruncated", False),
        "next_token": resp.get("NextContinuationToken"),
    }


@app.post("/buckets/{bucket}/objects")
def upload_object(
    bucket: str,
    file: UploadFile = File(...),
    key: Optional[str] = Form(None),
    c: Creds = Depends(creds),
):
    obj_key = (key or file.filename or f"upload-{int(time.time())}").lstrip("/")
    s3 = c.client()
    extra = {}
    if file.content_type and file.content_type != "application/octet-stream":
        extra["ContentType"] = file.content_type
    try:
        s3.upload_fileobj(file.file, bucket, obj_key, ExtraArgs=extra or None)
        head = s3.head_object(Bucket=bucket, Key=obj_key)
    except Exception as e:
        raise s3_error(e)
    meta = head.get("Metadata", {}) or {}
    return {
        "uploaded": True,
        "bucket": bucket,
        "key": obj_key,
        "size": head.get("ContentLength"),
        "etag": (head.get("ETag") or "").strip('"'),
        "content_type": head.get("ContentType"),
        "metadata": meta,
        "cid": meta.get("cid") or meta.get("ipfs-cid"),
        "public_url": f"{c.endpoint}/{bucket}/{obj_key}",
    }


@app.get("/buckets/{bucket}/objects/download")
def download_object(bucket: str, key: str = Query(...), c: Creds = Depends(creds)):
    try:
        obj = c.client().get_object(Bucket=bucket, Key=key)
    except Exception as e:
        raise s3_error(e)
    filename = key.rsplit("/", 1)[-1] or "download"
    return StreamingResponse(
        obj["Body"].iter_chunks(chunk_size=65536),
        media_type=obj.get("ContentType") or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **({"Content-Length": str(obj["ContentLength"])} if obj.get("ContentLength") else {}),
        },
    )


@app.get("/buckets/{bucket}/objects/meta")
def object_meta(bucket: str, key: str = Query(...), c: Creds = Depends(creds)):
    try:
        head = c.client().head_object(Bucket=bucket, Key=key)
    except Exception as e:
        raise s3_error(e)
    meta = head.get("Metadata", {}) or {}
    return {
        "bucket": bucket,
        "key": key,
        "size": head.get("ContentLength"),
        "etag": (head.get("ETag") or "").strip('"'),
        "content_type": head.get("ContentType"),
        "modified": head.get("LastModified").isoformat() if head.get("LastModified") else None,
        "metadata": meta,
        "cid": meta.get("cid") or meta.get("ipfs-cid"),
        "public_url": f"{c.endpoint}/{bucket}/{key}",
    }


@app.delete("/buckets/{bucket}/objects")
def delete_object(bucket: str, key: str = Query(...), c: Creds = Depends(creds)):
    try:
        c.client().delete_object(Bucket=bucket, Key=key)
    except Exception as e:
        raise s3_error(e)
    return {"deleted": True, "bucket": bucket, "key": key}


# ── Presigned URLs ───────────────────────────────────────────────────────

@app.get("/buckets/{bucket}/presign")
def presign(
    bucket: str,
    key: str = Query(...),
    op: str = Query("get", pattern="^(get|put)$"),
    expires: int = Query(3600, ge=60, le=604800),
    c: Creds = Depends(creds),
):
    method = "get_object" if op == "get" else "put_object"
    try:
        url = c.client().generate_presigned_url(
            method, Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires
        )
    except Exception as e:
        raise s3_error(e)
    return {"url": url, "op": op, "expires_in": expires}


# ── Bucket policy (public/private) ───────────────────────────────────────

class PolicyBody(BaseModel):
    public: bool


@app.get("/buckets/{bucket}/policy")
def get_policy(bucket: str, c: Creds = Depends(creds)):
    try:
        resp = c.client().get_bucket_policy(Bucket=bucket)
        policy = json.loads(resp.get("Policy") or "{}")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchBucketPolicy", "NoSuchPolicy"):
            return {"bucket": bucket, "public": False, "policy": None}
        raise s3_error(e)
    except Exception as e:
        raise s3_error(e)
    public = any(
        s.get("Effect") == "Allow" and s.get("Principal") in ("*", {"AWS": "*"})
        for s in policy.get("Statement", [])
    )
    return {"bucket": bucket, "public": public, "policy": policy}


@app.put("/buckets/{bucket}/policy")
def set_policy(bucket: str, body: PolicyBody, c: Creds = Depends(creds)):
    s3 = c.client()
    try:
        if body.public:
            s3.put_bucket_policy(Bucket=bucket, Policy=_public_policy(bucket))
        else:
            s3.delete_bucket_policy(Bucket=bucket)
    except Exception as e:
        raise s3_error(e)
    return {"bucket": bucket, "public": body.public}
