"""
DRF authentication built on the custom `Users` session model.

Why not DRF's built-in SessionAuthentication: that class authenticates
through django.contrib.auth, which is bypassed in this project (no Django
User model). But its CSRF enforcement is exactly what we want, so this
class reproduces it (CSRFCheck subclass, same as rest_framework).

 behaviour:
 - Session cookie with a valid `user_id` -> authenticates that user,
   sets `request.current_user` and enforces CSRF for unsafe methods.
 - No/invalid session -> returns None (anonymous; 401 vs 403 then comes
   from DRF's permission_denied logic, because successful_authenticator
   is empty whenever DEFAULT_AUTHENTICATION_CLASSES is non-empty).
"""

from django.middleware.csrf import CsrfViewMiddleware
from rest_framework import exceptions
from rest_framework.authentication import CSRFCheck, BaseAuthentication


class DummyGetResponse:
    def __call__(self, request):
        return None


class SessionUserAuthentication(BaseAuthentication):
    """
    Authenticate against `request.session["user_id"]` and enforce Django
    CSRF protection for authenticated unsafe requests.
    """

    def authenticate(self, request):
        from .permissions import get_current_user

        user = get_current_user(request)

        if user is None:
            # Unauthenticated; CSRF validation not required.
            return None

        self.enforce_csrf(request)

        return (user, None)

    def authenticate_header(self, request):
        """
        Without this DRF coerces NotAuthenticated (401) into 403. A custom
        scheme name keeps browsers from popping any auth dialog.
        """
        return 'Session realm="tournament"'

    def enforce_csrf(self, request):
        """
        Enforce CSRF validation for session-based authentication
        (same mechanism as rest_framework.SessionAuthentication).
        """

        class CSRFCheck(CsrfViewMiddleware):
            def _reject(self, xhr_request, reason):
                # Return the failure reason instead of an HttpResponse.
                return reason

        check = CSRFCheck(DummyGetResponse())
        # Populates request.META['CSRF_COOKIE'], which is used in process_view.
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            # CSRF failed: reject with an explicit error message.
            raise exceptions.PermissionDenied("CSRF Failed: %s" % reason)
