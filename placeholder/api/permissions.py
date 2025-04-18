from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from .utils import get_client_ip


class IsSecure(BasePermission):
    """
    Allows access only to secured request (HTTPS), or ip request is local
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not settings.API_REQUIRES_HTTPS:
            return True

        if not settings.TEST and get_client_ip(request).startswith('127.0.0.1'):
            return True

        if not settings.DEBUG and not request.is_secure():
            raise Response({'message': 'HTTPS connection is required'},
                           status_code=status.HTTP_403_FORBIDDEN)
        return True
