from typing import Any, Optional

from rest_framework import status
from rest_framework.response import Response

from django.forms import Form


class ErrorResponse(Response):
    def __init__(
        self,
        form: Optional[Form] = None,
        code: str = "invalid_request",
        message: str = "Failed to process request",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if not kwargs.get("status"):
            kwargs["status"] = status.HTTP_400_BAD_REQUEST

        super().__init__(*args, **kwargs)

        errors = []
        if form and form.errors.items():
            for field, validation_errors in form.errors.as_data().items():
                error = validation_errors[0]
                errors.append(
                    {
                        "code": error.code,
                        "message": error.message,
                        "field": field,
                    }
                )
        else:
            errors.append(
                {
                    "code": code,
                    "message": message,
                    "field": None,
                }
            )

        self.data = {"errors": errors}
