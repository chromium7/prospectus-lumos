from typing import Tuple

from rest_framework.exceptions import AuthenticationFailed
from rest_framework.authentication import BaseAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.request import Request

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.core.cache import cache


NOT_FOUND = 'NOT-FOUND'

def get_cache_key(token: str) -> str:
    key = 'api-auth:%s' % token
    return key


def get_token(auth: str) -> Token:
    cache_key = get_cache_key(auth)
    cached_token = cache.get(cache_key)
    if cached_token:
        if cached_token == NOT_FOUND:
            raise AuthenticationFailed('Invalid token')
        else:
            return cached_token

    try:
        token = Token.objects.select_related('user').get(key=auth)
    except Token.DoesNotExist:
        cache.set(cache_key, NOT_FOUND)
        raise AuthenticationFailed('Invalid token')

    cache.set(cache_key, token)
    return token


class UserTokenAuthentication(BaseAuthentication):
    model = Token

    def authenticate(self, request: Request) -> Tuple[AbstractBaseUser, Token]:
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if not auth_header:
            raise AuthenticationFailed('Invalid authorization header. No credentials provided.')

        auth_token = auth_header.split()
        if len(auth_token) != 2:
            raise AuthenticationFailed('Invalid authorization header. Invalid format.')

        token = get_token(auth_token[1])
        self.validate_user(token)
        return (token.user, token)

    def validate_user(self, token: Token) -> None:
        if not token.user.is_active:
            raise AuthenticationFailed(f'{token.user.name} is no longer an active merchant')

    def authenticate_header(self, request: Request) -> str:
        return 'Token'


class SingleTokenAuthentication(BaseAuthentication):
    """
    Parse header authorization and look for token
    `Authorization: token <token>`
    """

    token = settings.API_AUTHENTICATION_TOKEN

    def authenticate(self, request: Request) -> None:
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if not auth_header:
            raise AuthenticationFailed('Invalid authorization header. No credentials provided.')

        auth_token = auth_header.split()
        if len(auth_token) != 2:
            raise AuthenticationFailed('Invalid authorization header. Invalid format.')

        if auth_token[1] != self.token:
            raise AuthenticationFailed('Invalid token')
