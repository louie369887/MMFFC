"""Tests for the Modrinth API client (HTTP mocked with responses)."""

from __future__ import annotations

import json

import pytest
import responses

from mmffc.core.errors import NetworkError, NotFoundError
from mmffc.mods.modrinth import ModrinthClient


@responses.activate
def test_search_sends_nested_facets():
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={"total_hits": 1, "hits": [{"project_id": "X"}]},
    )
    client = ModrinthClient()
    client.search("sodium", facets=["loaders:fabric", "versions:1.20.1"])
    request = responses.calls[0].request
    assert "facets=%5B%5B%22loaders%3Afabric%22%5D" in request.url or (
        json.loads(
            request.url.split("facets=", 1)[1].split("&", 1)[0]
            .replace("%5B", "[")
            .replace("%5D", "]")
            .replace("%22", '"')
            .replace("%3A", ":")
        )
        == [["loaders:fabric"], ["versions:1.20.1"]]
    )


@responses.activate
def test_search_returns_hits():
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={
            "total_hits": 1,
            "hits": [
                {
                    "project_id": "AANobbMI",
                    "slug": "sodium",
                    "title": "Sodium",
                    "description": "fast",
                    "downloads": 100,
                    "follows": 10,
                }
            ],
        },
    )
    client = ModrinthClient()
    result = client.search("sodium", limit=5)
    assert result["total_hits"] == 1
    assert result["hits"][0]["slug"] == "sodium"


@responses.activate
def test_429_retries_with_retry_after():
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={"error": "rate limited"},
        status=429,
        headers={"Retry-After": "0"},
    )
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={"total_hits": 0, "hits": []},
    )
    client = ModrinthClient()
    result = client.search("x")
    assert result["total_hits"] == 0
    assert len(responses.calls) == 2


@responses.activate
def test_404_raises_not_found():
    responses.get(
        "https://api.modrinth.com/v2/project/unknown",
        json={"error": "not found"},
        status=404,
    )
    client = ModrinthClient()
    with pytest.raises(NotFoundError):
        client.get_project("unknown")


@responses.activate
def test_401_raises_network_error():
    responses.get(
        "https://api.modrinth.com/v2/project/x",
        json={"error": "unauthorized"},
        status=401,
    )
    client = ModrinthClient()
    with pytest.raises(NetworkError):
        client.get_project("x")


@responses.activate
def test_500_raises_network_error():
    responses.get(
        "https://api.modrinth.com/v2/project/x",
        json={"error": "boom"},
        status=500,
    )
    client = ModrinthClient()
    with pytest.raises(NetworkError):
        client.get_project("x")


@responses.activate
def test_token_header():
    responses.get(
        "https://api.modrinth.com/v2/project/x",
        json={"slug": "x"},
    )
    client = ModrinthClient(token="mrp_testtoken")
    client.get_project("x")
    assert responses.calls[0].request.headers["Authorization"] == "mrp_testtoken"


def test_sort_versions():
    versions = [
        {"version_number": "a", "version_type": "alpha", "date_published": "2026-02-01T00:00:00+00:00"},
        {"version_number": "r", "version_type": "release", "date_published": "2026-01-01T00:00:00+00:00"},
        {"version_number": "b", "version_type": "beta", "date_published": "2026-03-01T00:00:00+00:00"},
        {"version_number": "r2", "version_type": "release", "date_published": "2026-01-02T00:00:00+00:00"},
    ]
    sorted_versions = ModrinthClient.sort_versions(versions)
    assert [v["version_number"] for v in sorted_versions] == ["r2", "r", "b", "a"]
