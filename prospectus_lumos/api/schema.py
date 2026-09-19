"""drf-spectacular extensions describing hand-written API infrastructure."""

from typing import Any, Dict

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class SingleTokenAuthenticationScheme(OpenApiAuthenticationExtension):
    """Teaches the generator about `Authorization: token <token>`."""

    target_class = "prospectus_lumos.api.authentication.SingleTokenAuthentication"
    name = "singleTokenAuth"

    def get_security_definition(self, auto_schema: Any) -> Dict[str, Any]:
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Shared deployment token, sent as `Authorization: token <token>`.",
        }


class UserTokenAuthenticationScheme(OpenApiAuthenticationExtension):
    """Teaches the generator about per-user DRF tokens."""

    target_class = "prospectus_lumos.api.authentication.UserTokenAuthentication"
    name = "userTokenAuth"

    def get_security_definition(self, auto_schema: Any) -> Dict[str, Any]:
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Per-user token, sent as `Authorization: Token <token>`.",
        }
