"""Tests for src/api/auth.py — JWT token creation."""

from __future__ import annotations

import jwt
import pytest

from src.api.auth import create_jwt_token

ACCESS_KEY = "test_access_key_abc123"
SECRET_KEY = "test_secret_key_xyz789"


class TestCreateJwtToken:
    def test_returns_string_starting_with_bearer(self):
        token = create_jwt_token(ACCESS_KEY, SECRET_KEY)
        assert isinstance(token, str)
        assert token.startswith("Bearer ")

    def test_token_is_valid_jwt(self):
        bearer = create_jwt_token(ACCESS_KEY, SECRET_KEY)
        raw = bearer.removeprefix("Bearer ")
        payload = jwt.decode(raw, SECRET_KEY, algorithms=["HS256"])
        assert payload["access_key"] == ACCESS_KEY
        assert "nonce" in payload

    def test_without_query_params_no_query_hash(self):
        bearer = create_jwt_token(ACCESS_KEY, SECRET_KEY)
        raw = bearer.removeprefix("Bearer ")
        payload = jwt.decode(raw, SECRET_KEY, algorithms=["HS256"])
        assert "query_hash" not in payload
        assert "query_hash_alg" not in payload

    def test_with_query_params_includes_query_hash(self):
        params = {"market": "KRW-BTC", "side": "bid"}
        bearer = create_jwt_token(ACCESS_KEY, SECRET_KEY, query_params=params)
        raw = bearer.removeprefix("Bearer ")
        payload = jwt.decode(raw, SECRET_KEY, algorithms=["HS256"])
        assert "query_hash" in payload
        assert payload["query_hash_alg"] == "SHA512"

    def test_query_hash_is_sha512_hex(self):
        """query_hash must be a 128-character hex string (SHA-512 output)."""
        params = {"market": "KRW-ETH"}
        bearer = create_jwt_token(ACCESS_KEY, SECRET_KEY, query_params=params)
        raw = bearer.removeprefix("Bearer ")
        payload = jwt.decode(raw, SECRET_KEY, algorithms=["HS256"])
        query_hash = payload["query_hash"]
        assert len(query_hash) == 128
        assert all(c in "0123456789abcdef" for c in query_hash)

    def test_nonce_is_unique_per_call(self):
        bearer1 = create_jwt_token(ACCESS_KEY, SECRET_KEY)
        bearer2 = create_jwt_token(ACCESS_KEY, SECRET_KEY)
        raw1 = bearer1.removeprefix("Bearer ")
        raw2 = bearer2.removeprefix("Bearer ")
        p1 = jwt.decode(raw1, SECRET_KEY, algorithms=["HS256"])
        p2 = jwt.decode(raw2, SECRET_KEY, algorithms=["HS256"])
        assert p1["nonce"] != p2["nonce"]

    def test_empty_query_params_dict_treated_as_falsy(self):
        """An empty dict should not add query_hash (falsy branch)."""
        bearer = create_jwt_token(ACCESS_KEY, SECRET_KEY, query_params={})
        raw = bearer.removeprefix("Bearer ")
        payload = jwt.decode(raw, SECRET_KEY, algorithms=["HS256"])
        assert "query_hash" not in payload

    def test_different_secrets_produce_different_tokens(self):
        t1 = create_jwt_token(ACCESS_KEY, "secret_a")
        t2 = create_jwt_token(ACCESS_KEY, "secret_b")
        assert t1 != t2
