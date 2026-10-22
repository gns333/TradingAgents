"""Server-only CloudBase NoSQL HTTP transport, including real transactions.

Never retry an ambiguous write/commit. A per-operation receipt in the same
transaction lets callers resolve a lost commit response without replaying it.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import random
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

_LOGGER = logging.getLogger(__name__)
# Error responses may echo user input. Only diagnostic/schema vocabulary is
# retained; arbitrary values (including quoted strings) never enter logs.
_DIAGNOSTIC_WORDS = frozenset((  # noqa: SIM905 - grouped vocabulary is easier to audit
    "a an the is are was be been not no cannot can must should required missing "
    "invalid valid invalid_param parameter parameters param argument arguments "
    "error failed failure unsupported unexpected unknown malformed empty null redacted "
    "undefined too large small long short maximum minimum max min exceeds exceed "
    "size length limit offset bytes byte mb kb count timeout expired transaction "
    "transactionid transaction_id database collection collections document documents "
    "command commands batch write update updates insert inserts delete deletes "
    "find aggregate query filter projection order field fields direction data "
    "replacemode upsert multi returndoc q u _id _openid set unset inc push "
    "json ejson bson parse parsing serialize deserialize serialization deserialization "
    "type string number integer int int32 int64 double boolean object array value "
    "values key keys name names path format expected found received contains contain "
    "allowed supported only with without for in of to than at or and from by "
    "request response body payload property properties encoding utf utf8 utf16 "
    "numberint numberlong numberdouble date binary oid regex options "
    "结果 参数 无效 缺失 必须 不能为空 超过 长度 限制 类型 不支持 事务 过期"
).split())


def _diagnostic_message(value, private_values=()) -> str:
    if not isinstance(value, str):
        return "<unavailable>"
    for private in sorted(set(private_values), key=len, reverse=True):
        if private:
            value = value.replace(private, "<redacted>")
    value = re.sub(r"(['\"`]).*?\1", "<redacted>", value[:4096], flags=re.DOTALL)

    def token(match):
        word = match[0]
        return word if word.lower() in _DIAGNOSTIC_WORDS or (
            word.isascii() and word.isdigit() and len(word) <= 9
        ) else "<redacted>"

    value = re.sub(r"[\w-]+", token, value)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return re.sub(r"(?:<redacted>[ <>]*){2,}", "<redacted>", value)[:512]


def _safe_identifier(value) -> str:
    return value if isinstance(value, str) and re.fullmatch(
        r"(?:[A-Z][A-Z0-9_]{1,79}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", value
    ) else "<unavailable>"


def _request_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _request_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _request_values(item)
    elif value is not None:
        yield str(value)


def log_failure_stack(logger, exc, **context):
    """Keep call sites, not exception text/chains/locals that may hold secrets."""
    frames = traceback.extract_tb(exc.__traceback__)
    stack = "\n".join(f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in frames)
    logger.error("Analysis failure context=%s type=%s stack=\n%s",
                 json.dumps(context), type(exc).__name__, stack)


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

    def _log_error(self, method, path, body, params, response, error):
        # Endpoint templates conceal document IDs and transaction tokens.
        collection_match = re.fullmatch(r"/collections/([a-zA-Z0-9_]+)/documents(?:/[^/]+)?", path)
        collection = collection_match[1] if collection_match else None
        if collection_match:
            endpoint = "/collections/{collection}/documents" + ("/{id}" if path.count("/") == 4 else "")
        elif re.fullmatch(r"/transactions/[^/]+/(commit|rollback)", path):
            endpoint = "/transactions/{id}/" + path.rsplit("/", 1)[-1]
        else:
            endpoint = path if path in {"/commands", "/transactions"} else "<other>"
        try:
            request_bytes = len(json.dumps(body, allow_nan=False).encode("utf-8")) if body is not None else 0
        except (TypeError, ValueError):
            request_bytes = None
        commands = (body or {}).get("commands", []) if isinstance(body, dict) else []
        batches = []
        for command in commands[:20]:
            if not isinstance(command, dict):
                continue
            for operation, entries in (("update", "updates"), ("insert", "documents"), ("delete", "deletes")):
                if operation in command:
                    target = command[operation]
                    batches.append({"operation": operation,
                                    "collection": target if target in {
                                        "ta_control", "ta_users", "ta_models", "ta_whitelist", "ta_sessions",
                                        "ta_runs", "ta_events", "ta_reports", "ta_owners", "ta_operations",
                                    } else "<other>",
                                    "count": len(command[entries]) if isinstance(command.get(entries), list) else None})
        diagnostic = {
            "method": method, "endpoint": endpoint, "collection": collection,
            "status": response.status_code,
            "code": _safe_identifier(error.get("code") or "HTTP_ERROR"),
            "request_id": _safe_identifier(error.get("request_id") or response.headers.get("x-request-id", "")),
            "message": _diagnostic_message(error.get("message"), (
                self._api_key, *_request_values(body), *_request_values(params),
            )),
            "request_bytes": request_bytes, "command_count": len(commands), "batches": batches,
            "query_bytes": len(json.dumps(params).encode("utf-8")) if params is not None else 0,
            "in_transaction": bool((body or {}).get("transactionId") or (params or {}).get("transactionId")),
        }
        # Expected misses are part of normal get/upsert behavior, not failures.
        if response.status_code == 404 and error.get("code") == "DOCUMENT_NOT_FOUND":
            return
        stack = "\n".join(f"  {f.filename}:{f.lineno} in {f.name}" for f in traceback.extract_stack()[:-1])
        _LOGGER.error("CloudBase database request failed diagnostic=%s stack=\n%s",
                      json.dumps(diagnostic, ensure_ascii=False), stack)

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
            if not isinstance(error, dict):
                error = {}
            try:
                self._log_error(method, path, body, params, response, error)
            except Exception:  # Diagnostic failures must not change write/retry semantics.
                _LOGGER.error("CloudBase database diagnostic unavailable status=%s", response.status_code)
            raise DocumentDatabaseError(
                response.status_code, _safe_identifier(error.get("code") or "HTTP_ERROR"),
                _safe_identifier(error.get("request_id") or response.headers.get("x-request-id", "")),
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
