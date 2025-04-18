from typing import Any, Tuple

from django import forms
from django.contrib.postgres.fields import ArrayField
from django.db import models



class ChoicesField:

    def deconstruct(self) -> Tuple:
        name, path, args, kwargs = super().deconstruct()  # type: ignore
        kwargs.pop('choices', None)
        return (name, path, args, kwargs)


class ChoicesCharField(ChoicesField, models.CharField):
    pass


class ChoicesIntegerField(ChoicesField, models.IntegerField):
    pass


class ChoicesPositiveSmallIntegerField(ChoicesField, models.PositiveSmallIntegerField):
    pass


class ChoiceArrayField(ArrayField):
    """
    A field that allows us to store an array of choices.
    Uses Django's Postgres ArrayField
    and a MultipleChoiceField for its formfield.
    """

    def formfield(self, **kwargs: Any) -> forms.Form:
        if isinstance(self.base_field, models.IntegerField):
            kwargs.update({
                'form_class': forms.TypedMultipleChoiceField,
                'choices': self.base_field.choices,
                'coerce': int,
            })
        else:
            kwargs.update({
                'form_class': forms.MultipleChoiceField,
                'choices': self.base_field.choices,
            })

        return super().formfield(**kwargs)
