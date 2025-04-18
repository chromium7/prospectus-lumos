from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import SingleTokenAuthentication
from .parser import JSONParser
from .permissions import IsSecure


class BaseAPIView(APIView):

    permission_classes: tuple = (IsAuthenticated, IsSecure)
    authentication_classes: tuple = (SingleTokenAuthentication,)

    renderer_classes: tuple = (JSONRenderer,)
    parser_classes: tuple = (JSONParser,)


class Ping(BaseAPIView):
    permission_classes = tuple()

    def get(self, request: Request) -> Response:
        return Response({'status': 'ok'})

    def post(self, request: Request) -> Response:
        return Response({'status': 'ok'})
