from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin


class ValidationErrorPageMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        if isinstance(exception, ValidationError):
            return render(request, "core/error.html", {"errors": exception.messages}, status=400)
        return None
