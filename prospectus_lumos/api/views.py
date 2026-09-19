from drf_spectacular.utils import OpenApiResponse, extend_schema

from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from typing import Tuple, Any

from .authentication import SingleTokenAuthentication
from .parser import JSONParser
from .permissions import IsSecure


class BaseAPIView(APIView):
    permission_classes: Tuple[Any, ...] = (IsAuthenticated, IsSecure)
    authentication_classes: Tuple[Any, ...] = (SingleTokenAuthentication,)

    renderer_classes: Tuple[Any, ...] = (JSONRenderer,)
    parser_classes: Tuple[Any, ...] = (JSONParser,)


PING_SCHEMA = extend_schema(
    summary="Liveness probe",
    request=None,
    responses={200: OpenApiResponse(response={"type": "object", "properties": {"status": {"type": "string"}}})},
)


class Ping(BaseAPIView):
    permission_classes = ()

    @PING_SCHEMA
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})

    @PING_SCHEMA
    def post(self, request: Request) -> Response:
        return Response({"status": "ok"})
