from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.deprecation import MiddlewareMixin
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError
from django.contrib.auth.models import AnonymousUser

User = get_user_model()

class JWTCookieMiddleware(MiddlewareMixin):
    """
    Middleware to authenticate users using a JWT stored in a cookie.
    This replaces the standard Session-based AuthenticationMiddleware.
    """
    def process_request(self, request):
        # 1. Get token from cookies

        if request.path.startswith('/superadmin/'):
            return self.get_response(request)

        
        token = request.COOKIES.get('access_token')
        
        request.user = None
        
        if token:
            try:
                # 2. Validate token using SimpleJWT's AccessToken class
                access_token_obj = AccessToken(token)
                user_id = access_token_obj['user_id']
                
                # 3. Fetch user and attach to request
                try:
                    user = User.objects.get(id=user_id)
                    if user.is_active:
                        request.user = user
                except User.DoesNotExist:
                    pass
            except (TokenError, KeyError):
                # Token is invalid or expired
                pass
        
        # If no user was found/authenticated, set to AnonymousUser (if needed by Django)
        if request.user is None:
            from django.contrib.auth.models import AnonymousUser
            request.user = AnonymousUser()

    def process_response(self, request, response):
        return response
