from utils.response import APIError


def get_cognito_sub(event):
    claims = (((event.get("requestContext") or {}).get("authorizer") or {})
              .get("jwt") or {}).get("claims") or {}
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise APIError(401, "UNAUTHORIZED", "Verified authorizer sub is required.")
    return sub
