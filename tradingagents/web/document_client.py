"""Server-only CloudBase NoSQL HTTP transport, including real transactions.

Never retry an ambiguous write/commit. A per-operation receipt in the same
transaction lets callers resolve a lost commit response without replaying it.
"""

from __future__ import annotations

import contextlib
import copy
import json
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests


class DocumentDatabaseError(RuntimeError):
    def __init__(self, status: int, code: str, request_id: str = ""):
        self.status = status
        self.code = code
        super().__init__(f"CloudBase database: {code} (HTTP {status}, request {request_id})")


def decode_ejson(value):
    if isinstance(value, list):
        return [decode_ejson(item) for item in value]
    if isinstance(value, dict):
        if len(value) == 1:
            for key in ("$numberInt", "$numberLong"):
                if key in value:
                    return int(value[key])
            if "$numberDouble" in value:
                return float(value["$numberDouble"])
            if "$oid" in value:
                return value["$oid"]
        return {key: decode_ejson(item) for key, item in value.items()}
    return value


class DocumentClient:
    def __init__(self, env_id: str, api_key: str):
        if not re.fullmatch(r"[a-zA-Z0-9-]+", env_id) or not api_key:
            raise ValueError("CloudBase environment and server API key are required")
        self.base_url = (
            f"https://{env_id}.api.tcloudbasegateway.com/v1/database/"
            "instances/(default)/databases/(default)"
        )
        self._api_key = api_key
        self._local = threading.local()

    def request(self, method, path, *, body=None, params=None):
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        response = self._local.session.request(
            method, self.base_url + path,
            headers={"Authorization": "Bearer " + self._api_key},
            json=body, params=params, timeout=(5, 20),
        )
        if not response.ok:
            try:
                error = response.json()
            except ValueError:
                error = {}
            raise DocumentDatabaseError(
                response.status_code, str(error.get("code") or "HTTP_ERROR"),
                str(error.get("request_id") or response.headers.get("x-request-id", "")),
            )
        return decode_ejson(response.json()) if response.content else {}

    @staticmethod
    def path(collection, doc_id=None):
        path = "/collections/" + quote(collection, safe="") + "/documents"
        return path if doc_id is None else path + "/" + quote(str(doc_id), safe="")

    def get(self, collection, doc_id, tx=None):
        pending = getattr(self._local, "pending", {})
        key = (collection, str(doc_id))
        if tx and key in pending:
            return copy.deepcopy(pending[key])
        try:
            row = self.request("GET", self.path(collection, doc_id),
                               params={"transactionId": tx} if tx else None)
        except DocumentDatabaseError as exc:
            if exc.status == 404 and exc.code == "DOCUMENT_NOT_FOUND":
                return None
            raise
        row.pop("_id", None)
        row.pop("_openid", None)
        return row

    def put(self, collection, doc_id, data, tx=None):
        if tx and hasattr(self._local, "pending"):
            self._local.pending[(collection, str(doc_id))] = copy.deepcopy(data)
            return
        self.request("PATCH", self.path(collection, doc_id), body={
            "data": data, "replaceMode": True, "upsert": True,
            **({"transactionId": tx} if tx else {}),
        })

    def delete(self, collection, doc_id, tx=None):
        if tx and hasattr(self._local, "pending"):
            exists = self.get(collection, doc_id, tx) is not None
            self._local.pending[(collection, str(doc_id))] = None
            return exists
        try:
            result = self.request("DELETE", self.path(collection, doc_id),
                                  params={"transactionId": tx} if tx else None)
            return bool(result.get("deleted"))
        except DocumentDatabaseError as exc:
            if exc.status == 404 and exc.code == "DOCUMENT_NOT_FOUND":
                return False
            raise

    def query(self, collection, query=None, *, order=None, limit=None, tx=None,
              projection=None):
        rows = []
        while limit is None or len(rows) < limit:
            page_size = min(100, limit - len(rows)) if limit is not None else 100
            params = {"query": json.dumps(query or {}), "limit": page_size,
                      "offset": len(rows)}
            if order:
                params["order"] = json.dumps([
                    {"field": field, "direction": direction} for field, direction in order
                ])
            if projection:
                params["projection"] = json.dumps(projection)
            if tx:
                params["transactionId"] = tx
            page = self.request("GET", self.path(collection), params=params)["list"]
            for row in page:
                row.pop("_id", None)
                row.pop("_openid", None)
            rows.extend(page)
            if len(page) < page_size:
                break
        return rows

    def atomic(self, operation):
        receipt_id = str(uuid.uuid4())
        for attempt in range(5):
            tx = self.request("POST", "/transactions")["transactionId"]
            self._local.pending = {}
            try:
                result = operation(tx)
                expires = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp() * 1000)
                self.put("ta_operations", receipt_id, {
                    "result": result, "expires_at": {"$date": {"$numberLong": str(expires)}},
                }, tx)
                commands = []
                for (collection, doc_id), data in self._local.pending.items():
                    if data is None:
                        commands.append({"delete": collection, "deletes": [
                            {"q": {"_id": doc_id}, "limit": 1},
                        ]})
                    else:
                        commands.append({"update": collection, "updates": [
                            {"q": {"_id": doc_id}, "u": data, "upsert": True},
                        ]})
                response = self.request("POST", "/commands", body={
                    "commands": commands, "transactionId": tx,
                })
                for results in response.get("list", []):
                    for item in results:
                        if not item.get("ok", 1) or item.get("writeErrors"):
                            raise DocumentDatabaseError(500, "BATCH_WRITE_FAILED")
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.request("POST", f"/transactions/{tx}/rollback")
                if (isinstance(exc, DocumentDatabaseError)
                        and exc.code == "DATABASE_TRANSACTION_CONFLICT" and attempt < 4):
                    time.sleep(random.uniform(0.03, 0.1) * (attempt + 1))
                    continue
                raise
            finally:
                self._local.pending = {}
            try:
                self.request("POST", f"/transactions/{tx}/commit")
                return result
            except (requests.RequestException, DocumentDatabaseError) as exc:
                # Only a documented conflict proves the commit did not happen.
                if (isinstance(exc, DocumentDatabaseError)
                        and exc.code == "DATABASE_TRANSACTION_CONFLICT" and attempt < 4):
                    time.sleep(random.uniform(0.03, 0.1) * (attempt + 1))
                    continue
                receipt = self.get("ta_operations", receipt_id)
                if receipt is not None:
                    return receipt["result"]
                raise RuntimeError(
                    f"Database commit outcome unknown; operation {receipt_id}; not replayed"
                ) from exc
        raise RuntimeError("Database transaction retry limit reached")
