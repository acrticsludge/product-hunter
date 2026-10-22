"""The Qdrant adapter against an in-memory Qdrant that speaks the wire format.

No test here reaches a Qdrant server. The fake answers the real routes
with the real body shapes, so a test that passes says something about the
wire format and not only about the mock.
"""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest

from product_hunter.semantic.qdrant import (
    QdrantAdapter,
    QdrantConfigError,
    QdrantRequestFailed,
    QdrantUnavailable,
)

BASE_URL = "http://qdrant.test:6333"
COLLECTION = "product_hunter_evidence"
API_KEY = "qdrant-secret-value"


# --- the fake ---------------------------------------------------------------


def _ok(result: Any) -> httpx.Response:
    return httpx.Response(
        200, json={"status": "ok", "result": result, "time": 0.001}
    )


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status, json={"status": {"error": message}, "time": 0.001}
    )


def _matches(payload: dict[str, Any], filt: Any) -> bool:
    """Whether a payload passes a Qdrant filter."""
    if not filt:
        return True
    for clause in filt.get("must", []):
        key = clause.get("key")
        if "match" in clause:
            match = clause["match"]
            if "any" in match:
                if payload.get(key) not in match["any"]:
                    return False
            elif payload.get(key) != match.get("value"):
                return False
        elif "range" in clause:
            value = payload.get(key)
            window = clause["range"]
            if value is None:
                return False
            if "gte" in window and value < window["gte"]:
                return False
            if "lte" in window and value > window["lte"]:
                return False
    return True


def _cosine(left: list[float], right: list[float]) -> float:
    """The cosine of the angle between two vectors."""
    if not left or not right:
        return 0.0
    dot = math.fsum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class FakeQdrant:
    """A Qdrant server in memory."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self.queued: list[httpx.Response] = []
        self.query_endpoint = True

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = _body_of(request)
        self.bodies.append(body)
        if self.queued:
            return self.queued.pop(0)
        return self._route(request, body)

    def paths(self) -> list[str]:
        """The path of every call the adapter made."""
        return [request.url.path for request in self.requests]

    # --- the routes ---------------------------------------------------------

    def _route(
        self, request: httpx.Request, body: dict[str, Any]
    ) -> httpx.Response:
        method = request.method
        parts = [part for part in request.url.path.split("/") if part]
        if not parts or parts[0] != "collections":
            return _error(404, "not a collection route")
        if len(parts) == 1:
            if method == "GET":
                return _ok(
                    [
                        {"name": name, "status": "green"}
                        for name in self.collections
                    ]
                )
            return _error(405, "wrong method")
        name = parts[1]
        rest = parts[2:]
        if not rest:
            if method == "GET":
                return self._info(name)
            if method == "PUT":
                return self._create(name, body)
            if method == "DELETE":
                return self._delete(name)
        if rest == ["index"] and method == "PUT":
            return self._index(name, body)
        if rest == ["points"] and method == "PUT":
            return self._upsert(name, body)
        if rest == ["points", "delete"] and method == "POST":
            return self._delete_points(name, body)
        if rest == ["points", "query"] and method == "POST":
            if not self.query_endpoint:
                return _error(404, "unknown endpoint")
            return self._search(name, body)
        if rest == ["points", "search"] and method == "POST":
            return self._search(name, body)
        if rest == ["points", "scroll"] and method == "POST":
            return self._scroll(name, body)
        if rest == ["points", "count"] and method == "POST":
            return self._count(name, body)
        if len(rest) == 2 and rest[0] == "points" and method == "GET":
            return self._point(name, rest[1], request)
        return _error(404, "unknown route")

    def _create(
        self, name: str, body: dict[str, Any]
    ) -> httpx.Response:
        if name in self.collections:
            return _error(409, f"Collection `{name}` already exists!")
        vectors = body.get("vectors") or {}
        self.collections[name] = {
            "name": name,
            "config": {"params": {"vectors": vectors}},
            "metadata": body.get("metadata") or {},
            "points": {},
            "payload_indexes": [],
        }
        return _ok(True)

    def _info(self, name: str) -> httpx.Response:
        collection = self.collections.get(name)
        if collection is None:
            return _error(404, f"Not found: Collection `{name}` doesn't exist!")
        return _ok(
            {
                "status": "green",
                "optimizer_status": "ok",
                "vectors_count": len(collection["points"]),
                "config": collection["config"],
                "metadata": collection["metadata"],
                "payload_schema": {
                    field: {"data_type": "keyword"}
                    for field in collection["payload_indexes"]
                },
            }
        )

    def _delete(self, name: str) -> httpx.Response:
        if self.collections.pop(name, None) is None:
            return _error(404, f"Not found: Collection `{name}` doesn't exist!")
        return _ok(True)

    def _index(
        self, name: str, body: dict[str, Any]
    ) -> httpx.Response:
        collection = self._collection(name)
        if collection is None:
            return _error(404, f"Not found: Collection `{name}` doesn't exist!")
        field = body.get("field_name")
        if field not in collection["payload_indexes"]:
            collection["payload_indexes"].append(field)
        return _ok({"operation_id": 1, "status": "completed"})

    def _upsert(
        self, name: str, body: dict[str, Any]
    ) -> httpx.Response:
        collection = self._collection(name)
        if collection is None:
            return _error(404, f"Not found: Collection `{name}` doesn't exist!")
        for point in body.get("points", []):
            collection["points"][str(point["id"])] = {
                "id": point["id"],
                "vector": list(point.get("vector") or []),
                "payload": dict(point.get("payload") or {}),
            }
        return _ok({"operation_id": 1, "status": "completed"})

    def _delete_points(
        self, name: str, body: dict[str, Any]
    ) -> httpx.Response:
        collection = self._collection(name)
        if collection is None:
            return _error(404, f"Not found: Collection `{name}` doesn't exist!")
        for point_id in body.get("points", []):
            collection["points"].pop(str(point_id), None)
        return _ok({"operation_id": 1, "status": "completed"})

    def _search(
        self, name: str, body: dict[str, Any]
    ) -> httpx.Response:
        collection = self._collection(name)
        if collection is None:
            return _error(404, f"Not found: Collection `{name}` doesn't exist!")
        query = body.get("query")
        if query is None:
            query = body.get("vector")
        limit = int(body.get("limit", 10))
        threshold = body.get("score_threshold")
        scored: list[dict[str, Any]] = []
        for point in collection["points"].values():
            if not _matches(point["payload"], body.get("filter")):
                continue
            score = _cosine(list(query or []), point["vector"])
            if threshold is not None and score < float(threshold):
                continue
            scored.append(
                {
                    "id": point["id"],
                    "version": 0,
                    "score": score,
                    "payload": point["payload"],
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return _ok(scored[:limit])

    def _scroll(
        self, name: str, body: dict[str, Any]
    ) -> httpx.Response:
        collection = self._collection(name)
        if collection is None:
            return _error(404, f"Not found: Collection `{name}` doesn't exist!")
        limit = int(body.get("limit", 10))
        with_vector = bool(body.get("with_vector"))
        points: list[dict[str, Any]] = []
        for point in collection["points"].values():
            if not _matches(point["payload"], body.get("filter")):
                continue
            entry: dict[str, Any] = {"id": point["id"], "payload": point["payload"]}
            if with_vector:
                entry["vector"] = point["vector"]
            points.append(entry)
        return _ok({"points": points[:limit], "next_page_offset": None})

    def _count(
        self, name: str, body: dict[str, Any]
    ) -> httpx.Response:
        collection = self._collection(name)
        if collection is None:
            return _error(404, f"Not found: Collection `{name}` doesn't exist!")
        matching = sum(
            1
            for point in collection["points"].values()
            if _matches(point["payload"], body.get("filter"))
        )
        return _ok({"count": matching})

    def _point(
        self, name: str, point_id: str, request: httpx.Request
    ) -> httpx.Response:
        collection = self._collection(name)
        point = collection["points"].get(str(point_id)) if collection else None
        if point is None:
            return _error(404, f"Point `{point_id}` is not found")
        answer: dict[str, Any] = {"id": point["id"], "payload": point["payload"]}
        # The server only sends the vector when it is asked for.
        if request.url.params.get("with_vector") == "true":
            answer["vector"] = point["vector"]
        return _ok(answer)

    def _collection(self, name: str) -> dict[str, Any] | None:
        return self.collections.get(name)


def _body_of(request: httpx.Request) -> dict[str, Any]:
    """The body of a request, or nothing when it carries none."""
    if not request.content:
        return {}
    try:
        body = json.loads(request.content)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class Sleeps:
    """A sleep function that records instead of waiting."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _adapter(fake: FakeQdrant, **kwargs) -> QdrantAdapter:
    """An adapter over the fake, with a sleep that does not wait."""
    kwargs.setdefault("sleep", Sleeps())
    return QdrantAdapter(BASE_URL, transport=fake.transport(), **kwargs)


def _point(point_id: str, vector: list[float], **payload) -> dict[str, Any]:
    """One point in the wire shape."""
    body = {"entity_id": point_id, "source_id": "hn_algolia"}
    body.update(payload)
    return {"id": point_id, "vector": vector, "payload": body}


# --- the failure handling ---------------------------------------------------


def test_a_refused_connection_is_unavailable():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter = QdrantAdapter(
        BASE_URL, transport=httpx.MockTransport(refuse), sleep=Sleeps()
    )
    with pytest.raises(QdrantUnavailable):
        adapter.list_collections()


def test_a_refused_connection_spends_the_budget():
    calls: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("connection refused", request=request)

    adapter = QdrantAdapter(
        BASE_URL,
        transport=httpx.MockTransport(refuse),
        sleep=Sleeps(),
        max_attempts=3,
    )
    with pytest.raises(QdrantUnavailable):
        adapter.list_collections()
    assert len(calls) == 3


def test_a_server_error_is_retried_and_then_succeeds():
    fake = FakeQdrant()
    fake.queued.append(_error(500, "internal error"))
    sleeps = Sleeps()
    adapter = _adapter(fake, sleep=sleeps)

    assert adapter.list_collections() == []
    assert len(fake.requests) == 2
    assert sleeps.calls == [1.0]


def test_a_rate_limit_is_retried():
    fake = FakeQdrant()
    fake.queued.append(_error(429, "too many requests"))
    sleeps = Sleeps()
    adapter = _adapter(fake, sleep=sleeps)

    adapter.list_collections()
    assert len(fake.requests) == 2
    assert sleeps.calls == [1.0]


def test_a_refusal_is_not_retried():
    fake = FakeQdrant()
    fake.queued.append(_error(401, "wrong api key"))
    sleeps = Sleeps()
    adapter = _adapter(fake, sleep=sleeps)

    with pytest.raises(QdrantRequestFailed):
        adapter.list_collections()
    assert len(fake.requests) == 1
    assert sleeps.calls == []


def test_a_server_error_that_never_ends_fails_the_request():
    fake = FakeQdrant()
    fake.queued.extend([_error(503, "unavailable") for _ in range(4)])
    adapter = _adapter(fake, max_attempts=4)

    with pytest.raises(QdrantRequestFailed):
        adapter.list_collections()
    assert len(fake.requests) == 4


def test_the_backoff_doubles_and_is_capped():
    fake = FakeQdrant()
    fake.queued.extend([_error(500, "internal error") for _ in range(3)])
    sleeps = Sleeps()
    adapter = _adapter(fake, sleep=sleeps, backoff_seconds=40, max_attempts=3)

    with pytest.raises(QdrantRequestFailed):
        adapter.list_collections()
    assert sleeps.calls == [40.0, 60.0]


# --- the collection ---------------------------------------------------------


def test_the_collection_is_created_with_its_size_distance_and_metadata():
    fake = FakeQdrant()
    adapter = _adapter(fake)

    adapter.create_collection(
        COLLECTION,
        size=384,
        distance="Cosine",
        metadata={"space_id": "space-1"},
    )

    assert fake.bodies[0] == {
        "vectors": {"size": 384, "distance": "Cosine"},
        "metadata": {"space_id": "space-1"},
    }
    info = adapter.collection_info(COLLECTION)
    assert info is not None
    assert info["metadata"] == {"space_id": "space-1"}
    assert info["config"]["params"]["vectors"] == {
        "size": 384,
        "distance": "Cosine",
    }


def test_an_existing_collection_is_not_an_error():
    fake = FakeQdrant()
    adapter = _adapter(fake)

    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})

    assert adapter.collection_exists(COLLECTION) is True
    assert len(fake.collections[COLLECTION]["points"]) == 0


def test_a_missing_collection_is_none():
    fake = FakeQdrant()
    adapter = _adapter(fake)

    assert adapter.collection_info(COLLECTION) is None
    assert adapter.collection_exists(COLLECTION) is False


def test_the_collections_are_listed():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})

    assert adapter.list_collections() == [COLLECTION]


def test_a_collection_is_deleted():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})

    adapter.delete_collection(COLLECTION)

    assert adapter.collection_exists(COLLECTION) is False


def test_a_payload_index_is_created_for_each_field():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})

    adapter.create_payload_index(COLLECTION, "entity_type")
    adapter.create_payload_index(COLLECTION, "source_id")

    assert fake.bodies[1] == {
        "field_name": "entity_type",
        "field_schema": "keyword",
    }
    assert fake.collections[COLLECTION]["payload_indexes"] == [
        "entity_type",
        "source_id",
    ]
    assert fake.requests[1].url.params["wait"] == "true"


# --- the points -------------------------------------------------------------


def test_points_are_upserted():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})

    adapter.upsert(
        COLLECTION,
        [
            _point("p1", [1.0, 0.0], entity_type="evidence"),
            _point("p2", [0.0, 1.0], entity_type="pain_point"),
        ],
    )

    assert fake.bodies[1]["points"][0]["id"] == "p1"
    assert fake.bodies[1]["points"][0]["vector"] == [1.0, 0.0]
    assert fake.bodies[1]["points"][0]["payload"]["entity_type"] == "evidence"
    assert adapter.count(COLLECTION) == 2


def test_the_same_point_id_replaces_the_point():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})

    adapter.upsert(COLLECTION, [_point("p1", [1.0, 0.0])])
    adapter.upsert(COLLECTION, [_point("p1", [0.5, 0.5])])

    assert adapter.count(COLLECTION) == 1
    point = adapter.get_point(COLLECTION, "p1", with_vector=True)
    assert point is not None
    assert point["vector"] == [0.5, 0.5]


def test_a_point_carries_its_vector_only_when_it_is_asked_for():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})
    adapter.upsert(COLLECTION, [_point("p1", [1.0, 0.0])])

    with_vector = adapter.get_point(COLLECTION, "p1", with_vector=True)
    assert fake.requests[-1].url.params.get("with_vector") == "true"
    without_vector = adapter.get_point(COLLECTION, "p1")

    assert with_vector is not None
    assert with_vector["vector"] == [1.0, 0.0]
    assert without_vector is not None
    assert "vector" not in without_vector
    assert "with_vector" not in fake.requests[-1].url.params


def test_points_are_deleted_by_id():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})
    adapter.upsert(COLLECTION, [_point("p1", [1.0, 0.0]), _point("p2", [0.0, 1.0])])

    adapter.delete_points(COLLECTION, ["p1"])

    assert fake.bodies[2] == {"points": ["p1"]}
    assert adapter.count(COLLECTION) == 1
    assert adapter.get_point(COLLECTION, "p1") is None


def test_a_missing_point_is_none():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})

    assert adapter.get_point(COLLECTION, "nope") is None


# --- the search -------------------------------------------------------------


def test_search_returns_the_score_and_the_payload():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})
    adapter.upsert(
        COLLECTION,
        [
            _point("p1", [1.0, 0.0], entity_type="evidence"),
            _point("p2", [0.0, 1.0], entity_type="pain_point"),
        ],
    )

    hits = adapter.search(COLLECTION, [1.0, 0.0], limit=2)

    assert [hit["id"] for hit in hits] == ["p1", "p2"]
    assert hits[0]["score"] == pytest.approx(1.0)
    assert hits[0]["payload"]["entity_type"] == "evidence"
    body = fake.bodies[2]
    assert body["query"] == [1.0, 0.0]
    assert body["limit"] == 2
    assert body["with_payload"] is True


def test_the_search_body_carries_the_filter_and_the_threshold():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})

    adapter.search(
        COLLECTION,
        [1.0, 0.0],
        filter={"must": [{"key": "source_id", "match": {"value": "hn_algolia"}}]},
        score_threshold=0.5,
    )

    body = fake.bodies[1]
    assert body["filter"] == {
        "must": [{"key": "source_id", "match": {"value": "hn_algolia"}}]
    }
    assert body["score_threshold"] == 0.5


def test_a_filter_narrows_the_search():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})
    adapter.upsert(
        COLLECTION,
        [
            _point("p1", [1.0, 0.0], source_id="hn_algolia"),
            _point("p2", [1.0, 0.0], source_id="github_issues"),
        ],
    )

    hits = adapter.search(
        COLLECTION,
        [1.0, 0.0],
        filter={"must": [{"key": "source_id", "match": {"value": "hn_algolia"}}]},
    )

    assert [hit["id"] for hit in hits] == ["p1"]


def test_search_falls_back_to_the_old_endpoint():
    fake = FakeQdrant()
    fake.query_endpoint = False
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})
    adapter.upsert(COLLECTION, [_point("p1", [1.0, 0.0])])

    hits = adapter.search(COLLECTION, [1.0, 0.0])

    assert [hit["id"] for hit in hits] == ["p1"]
    assert fake.paths()[-1] == f"/collections/{COLLECTION}/points/search"
    assert fake.bodies[-1]["vector"] == [1.0, 0.0]
    assert "query" not in fake.bodies[-1]


# --- the scroll and the count -----------------------------------------------


def test_scroll_returns_the_points():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})
    adapter.upsert(COLLECTION, [_point("p1", [1.0, 0.0]), _point("p2", [0.0, 1.0])])

    points = adapter.scroll(COLLECTION, limit=10, with_vector=True)

    assert sorted(point["id"] for point in points) == ["p1", "p2"]
    assert points[0]["payload"]["entity_id"] in {"p1", "p2"}
    assert fake.bodies[2] == {
        "limit": 10,
        "with_payload": True,
        "with_vector": True,
    }


def test_count_returns_the_count():
    fake = FakeQdrant()
    adapter = _adapter(fake)
    adapter.create_collection(COLLECTION, size=384, distance="Cosine", metadata={})
    adapter.upsert(COLLECTION, [_point("p1", [1.0, 0.0]), _point("p2", [0.0, 1.0])])

    assert adapter.count(COLLECTION) == 2
    assert fake.bodies[2]["exact"] is True


# --- the api key ------------------------------------------------------------


def test_the_api_key_travels_in_the_header():
    fake = FakeQdrant()
    adapter = _adapter(fake, api_key=API_KEY)

    adapter.list_collections()

    assert fake.requests[0].headers["api-key"] == API_KEY


def test_the_api_key_is_in_no_message():
    fake = FakeQdrant()
    fake.queued.append(
        httpx.Response(
            401,
            json={"status": {"error": f"the key {API_KEY} is not welcome"}},
        )
    )
    adapter = _adapter(fake, api_key=API_KEY)

    with pytest.raises(QdrantRequestFailed) as caught:
        adapter.list_collections()

    assert API_KEY not in str(caught.value)
    assert API_KEY not in caught.value.detail
    assert "401" in str(caught.value)


# --- the base url -----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://qdrant.test:6333",
        "qdrant.test:6333",
        "http://user:pass@qdrant.test:6333",
        "http://",
    ],
)
def test_a_base_url_the_adapter_cannot_use_is_refused(url):
    with pytest.raises(QdrantConfigError):
        QdrantAdapter(url)
