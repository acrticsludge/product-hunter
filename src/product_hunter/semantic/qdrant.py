"""The Qdrant REST adapter: the transport, the collection, the points.

The adapter holds no product state. The collection name, the vector size
and the distance are arguments, and the space belongs to the collection
metadata. The api key travels in one header and is taken out of every
message this module builds.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from urllib.parse import quote, urlsplit

import httpx

# The status codes that are worth another try.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# The longest a retry waits before the next try.
_MAX_BACKOFF_SECONDS = 60.0


class QdrantError(Exception):
    """A Qdrant call failed."""

    reason_code = "qdrant_error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


class QdrantUnavailable(QdrantError):
    """The server could not be reached."""

    reason_code = "qdrant_unavailable"


class QdrantRequestFailed(QdrantError):
    """The server refused the request, or could not serve it."""

    reason_code = "qdrant_request_failed"


class QdrantConfigError(QdrantError):
    """The adapter was given a base url it cannot use."""

    reason_code = "qdrant_config_invalid"


class QdrantAdapter:
    """The Qdrant REST API over httpx.

    The transport and the sleep function are arguments, so the retry budget
    can be driven without a server and without waiting.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_attempts: int = 4,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = _checked_base_url(base_url)
        self._api_key = api_key
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            headers=_headers(api_key),
        )

    # --- the collection -----------------------------------------------------

    def list_collections(self) -> list[str]:
        """The names of the collections the server holds."""
        path = _route("collections")
        result = self._result("GET", path, self._send("GET", path))
        if not isinstance(result, list):
            raise QdrantRequestFailed("the collection list is not a list")
        return [
            str(entry["name"])
            for entry in result
            if isinstance(entry, dict) and entry.get("name")
        ]

    def collection_exists(self, name: str) -> bool:
        """Whether the collection is there."""
        return self.collection_info(name) is not None

    def collection_info(self, name: str) -> dict[str, object] | None:
        """What the server holds about the collection, or None."""
        path = _route("collections", name)
        response = self._send("GET", path)
        if response.status_code == 404:
            return None
        result = self._result("GET", path, response)
        if not isinstance(result, dict):
            raise QdrantRequestFailed(
                f"the collection info of {name!r} is not an object"
            )
        return result

    def create_collection(
        self,
        name: str,
        *,
        size: int,
        distance: str,
        metadata: dict[str, object],
    ) -> None:
        """Create the collection. An existing collection is not an error."""
        path = _route("collections", name)
        body: dict[str, object] = {
            "vectors": {"size": size, "distance": distance},
            "metadata": metadata,
        }
        response = self._send("PUT", path, body=body)
        if response.status_code == 409:
            return
        self._result("PUT", path, response)

    def delete_collection(self, name: str) -> None:
        """Drop the collection."""
        path = _route("collections", name)
        self._result("DELETE", path, self._send("DELETE", path))

    def create_payload_index(
        self, name: str, field: str, *, schema: str = "keyword"
    ) -> None:
        """Index one payload field, so a filter on it does not scan."""
        path = _route("collections", name, "index")
        body: dict[str, object] = {"field_name": field, "field_schema": schema}
        self._result(
            "PUT",
            path,
            self._send("PUT", path, body=body, params={"wait": "true"}),
        )

    # --- the points ---------------------------------------------------------

    def upsert(self, name: str, points: list[dict[str, object]]) -> None:
        """Write the points, replacing the ones with the same id."""
        path = _route("collections", name, "points")
        body: dict[str, object] = {"points": points}
        self._result(
            "PUT",
            path,
            self._send("PUT", path, body=body, params={"wait": "true"}),
        )

    def delete_points(self, name: str, ids: list[str]) -> None:
        """Remove the points of the given ids."""
        path = _route("collections", name, "points", "delete")
        body: dict[str, object] = {"points": list(ids)}
        self._result(
            "POST",
            path,
            self._send("POST", path, body=body, params={"wait": "true"}),
        )

    def search(
        self,
        name: str,
        vector: Sequence[float],
        *,
        limit: int = 10,
        filter: dict[str, object] | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, object]]:
        """The points nearest the vector, with their score and payload.

        The query endpoint arrived in Qdrant 1.10 and the older search
        endpoint still answers on every server since 1.0, so a 404 on the
        first one falls back to the second.
        """
        body: dict[str, object] = {
            "query": [float(value) for value in vector],
            "limit": limit,
            "with_payload": True,
        }
        if filter:
            body["filter"] = filter
        if score_threshold is not None:
            body["score_threshold"] = score_threshold
        path = _route("collections", name, "points", "query")
        response = self._send("POST", path, body=body)
        if response.status_code == 404:
            body["vector"] = body.pop("query")
            path = _route("collections", name, "points", "search")
            response = self._send("POST", path, body=body)
        result = self._result("POST", path, response)
        if not isinstance(result, list):
            raise QdrantRequestFailed("the search result is not a list")
        return [dict(point) for point in result if isinstance(point, dict)]

    def scroll(
        self,
        name: str,
        *,
        limit: int = 100,
        filter: dict[str, object] | None = None,
        with_vector: bool = False,
    ) -> list[dict[str, object]]:
        """The points of the collection, in no particular order."""
        body: dict[str, object] = {
            "limit": limit,
            "with_payload": True,
            "with_vector": with_vector,
        }
        if filter:
            body["filter"] = filter
        path = _route("collections", name, "points", "scroll")
        result = self._result("POST", path, self._send("POST", path, body=body))
        points = result.get("points") if isinstance(result, dict) else None
        if not isinstance(points, list):
            raise QdrantRequestFailed("the scroll result holds no points")
        return [dict(point) for point in points if isinstance(point, dict)]

    def count(
        self, name: str, *, filter: dict[str, object] | None = None
    ) -> int:
        """How many points the collection holds."""
        body: dict[str, object] = {"exact": True}
        if filter:
            body["filter"] = filter
        path = _route("collections", name, "points", "count")
        result = self._result("POST", path, self._send("POST", path, body=body))
        if not isinstance(result, dict):
            raise QdrantRequestFailed("the count result is not an object")
        return int(result.get("count", 0))

    def get_point(
        self, name: str, point_id: str, *, with_vector: bool = False
    ) -> dict[str, object] | None:
        """The point of the given id, or None.

        The vector comes back only when it is asked for: the point's vector
        is large and the caller usually wants the payload.
        """
        path = _route("collections", name, "points", point_id)
        params = {"with_vector": "true"} if with_vector else None
        response = self._send("GET", path, params=params)
        if response.status_code == 404:
            return None
        result = self._result("GET", path, response)
        if not isinstance(result, dict):
            raise QdrantRequestFailed(f"the point {point_id!r} is not an object")
        return result

    def close(self) -> None:
        """Close the connection to the server."""
        self._client.close()

    # --- the transport ------------------------------------------------------

    def _send(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """One call, with the retry budget applied.

        A transport failure and a retryable status are both faults, and both
        spend the budget. Every other status comes back to the caller, which
        knows what a 404 means on its own route.
        """
        failure: Exception = QdrantError(f"{method} {path} failed")
        for attempt in range(self._max_attempts):
            try:
                response = self._client.request(
                    method, path, json=body, params=params
                )
            except httpx.TransportError as exc:
                failure = QdrantUnavailable(
                    self._scrub(f"{method} {path} could not be reached: {exc}")
                )
            else:
                if response.status_code not in _RETRY_STATUSES:
                    return response
                failure = QdrantRequestFailed(self._describe(method, path, response))
            if attempt + 1 < self._max_attempts:
                self._sleep(self._backoff(attempt))
        raise failure

    def _backoff(self, attempt: int) -> float:
        """How long to wait before the next try."""
        return min(self._backoff_seconds * (2**attempt), _MAX_BACKOFF_SECONDS)

    def _result(
        self, method: str, path: str, response: httpx.Response
    ) -> object:
        """The result of a call, or a refusal."""
        if not response.is_success:
            raise QdrantRequestFailed(self._describe(method, path, response))
        body = _json_body(response)
        if isinstance(body, dict) and "result" in body:
            return body["result"]
        return body

    def _describe(self, method: str, path: str, response: httpx.Response) -> str:
        """The failure in one line, with the api key taken out."""
        detail = f"{method} {path} answered {response.status_code}"
        reason = _reason_of(response)
        if reason:
            detail = f"{detail}: {reason}"
        return self._scrub(detail)

    def _scrub(self, text: str) -> str:
        """The text with the api key taken out."""
        if not self._api_key:
            return text
        return text.replace(self._api_key, "[redacted]")


def vector_size(info: dict[str, object]) -> int | None:
    """The width of the vectors a collection holds, from its info."""
    config = info.get("config")
    if not isinstance(config, dict):
        return None
    params = config.get("params")
    if not isinstance(params, dict):
        return None
    vectors = params.get("vectors")
    if not isinstance(vectors, dict):
        return None
    size = vectors.get("size")
    return int(size) if isinstance(size, int) else None


def _checked_base_url(base_url: str) -> str:
    """The base url, refused when it cannot be used.

    A url with a scheme the adapter cannot speak, or one that carries a
    password in it, is a config mistake rather than a server fault.
    """
    try:
        parts = urlsplit(base_url)
    except ValueError as exc:
        raise QdrantConfigError(f"the qdrant url cannot be read: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise QdrantConfigError(
            f"the qdrant url must be http or https, not {parts.scheme or 'nothing'}"
        )
    if not parts.netloc:
        raise QdrantConfigError("the qdrant url has no host")
    if parts.username or parts.password or "@" in parts.netloc:
        raise QdrantConfigError("the qdrant url must not carry userinfo")
    return base_url.rstrip("/")


def _headers(api_key: str | None) -> dict[str, str]:
    """The headers every call carries."""
    headers = {"accept": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    return headers


def _route(*parts: str) -> str:
    """The path of a route, relative to the base url."""
    return "/".join(quote(part, safe="") for part in parts)


def _json_body(response: httpx.Response) -> object:
    """The parsed body, or nothing when the body is not json."""
    try:
        return response.json()
    except ValueError:
        return {}


def _reason_of(response: httpx.Response) -> str:
    """What the server said went wrong, if it said."""
    body = _json_body(response)
    if not isinstance(body, dict):
        return ""
    status = body.get("status")
    if isinstance(status, dict):
        error = status.get("error")
        if isinstance(error, str):
            return error
    if isinstance(status, str):
        return status
    return ""
