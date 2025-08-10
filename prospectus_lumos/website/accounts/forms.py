from __future__ import annotations
from typing import Any

from django import forms

from prospectus_lumos.apps.accounts.models import GoogleDriveCredentials, DocumentSource


class GoogleDriveCredentialsForm(forms.ModelForm):
    class Meta:
        model = GoogleDriveCredentials
        fields = [
            "service_account_file",
            "drive_folder_url",
            "is_active",
        ]
        widgets = {
            "service_account_file": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "drive_folder_url": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://drive.google.com/drive/folders/..."}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class DocumentSourceForm(forms.ModelForm):
    class Meta:
        model = DocumentSource
        fields = [
            "source_type",
            "name",
            "google_credentials",
            "is_active",
        ]
        widgets = {
            "source_type": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., Main Drive Folder"}),
            "google_credentials": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        # Limit google credentials choices to current user's
        if user is not None:
            self.fields["google_credentials"].queryset = user.google_drive_credentials.all()  # type: ignore
