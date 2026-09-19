"""Test-only URLconf exercising each error class through the real handler."""

from typing import Any

from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from django.urls import path


class SampleSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1)


class ValidationView(APIView):
    permission_classes: Any = (AllowAny,)

    def post(self, request: Request) -> Response:
        serializer = SampleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


class ForbiddenView(APIView):
    permission_classes: Any = (AllowAny,)

    def get(self, request: Request) -> Response:
        raise PermissionDenied()


class MissingView(APIView):
    permission_classes: Any = (AllowAny,)

    def get(self, request: Request) -> Response:
        raise NotFound()


urlpatterns = [
    path("validation", ValidationView.as_view()),
    path("forbidden", ForbiddenView.as_view()),
    path("missing", MissingView.as_view()),
]
