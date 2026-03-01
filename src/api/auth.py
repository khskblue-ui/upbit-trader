import hashlib
import logging
import uuid
from urllib.parse import urlencode

import jwt

logger = logging.getLogger(__name__)


def create_jwt_token(
    access_key: str,
    secret_key: str,
    query_params: dict | None = None,
) -> str:
    """Create a JWT Bearer token for Upbit API authentication.

    Args:
        access_key: Upbit API access key.
        secret_key: Upbit API secret key.
        query_params: Optional query parameters to include in the token payload
                      as a query hash (SHA512).

    Returns:
        Bearer token string in the format "Bearer {token}".
    """
    payload: dict = {
        "access_key": access_key,
        "nonce": str(uuid.uuid4()),
    }

    if query_params:
        query_string = urlencode(query_params).encode()
        query_hash = hashlib.sha512(query_string).hexdigest()
        payload["query_hash"] = query_hash
        payload["query_hash_alg"] = "SHA512"
        logger.debug("Created query hash for params: %s", list(query_params.keys()))

    token = jwt.encode(payload, secret_key, algorithm="HS256")
    logger.debug("JWT token created for access_key: %s...", access_key[:8])

    return f"Bearer {token}"
