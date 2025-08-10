from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from prospectus_lumos.apps.accounts.models import DocumentSource, GoogleDriveCredentials
from prospectus_lumos.core.utils import TypedHttpRequest

from .forms import DocumentSourceForm, GoogleDriveCredentialsForm


@login_required
def google_credentials_view(request: TypedHttpRequest) -> HttpResponse:
    credentials = GoogleDriveCredentials.objects.filter(user=request.user).first()

    if request.method == "POST":
        form = GoogleDriveCredentialsForm(request.POST, request.FILES, instance=credentials)
        if form.is_valid():
            creds = form.save(commit=False)
            creds.user = request.user
            creds.save()
            messages.success(request, "Google Drive credentials updated")
            return redirect("google_credentials")
    else:
        form = GoogleDriveCredentialsForm(instance=credentials)

    return render(
        request,
        "accounts/google_credentials.html",
        {"form": form, "selected_tab": "google_credentials", "credentials": credentials},
    )


@login_required
def source_list_view(request: TypedHttpRequest) -> HttpResponse:
    sources = DocumentSource.objects.filter(user=request.user).order_by("-updated_at")
    return render(request, "accounts/source_list.html", {"sources": sources, "selected_tab": "sources"})


@login_required
@require_http_methods(["GET", "POST"])
def source_create_view(request: TypedHttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = DocumentSourceForm(request.POST, user=request.user)
        if form.is_valid():
            source = form.save(commit=False)
            source.user = request.user
            # Validate google drive requirement
            if source.source_type == "google_drive" and not source.google_credentials:
                messages.error(request, "Google Drive source requires linked Google credentials")
            else:
                source.save()
                messages.success(request, "Source created")
                return redirect("source_list")
    else:
        form = DocumentSourceForm(user=request.user)

    return render(request, "accounts/source_form.html", {"form": form, "create": True, "selected_tab": "sources"})


@login_required
@require_http_methods(["GET", "POST"])
def source_update_view(request: TypedHttpRequest, pk: int) -> HttpResponse:
    source = get_object_or_404(DocumentSource, pk=pk, user=request.user)
    if request.method == "POST":
        form = DocumentSourceForm(request.POST, instance=source, user=request.user)
        if form.is_valid():
            updated_source = form.save(commit=False)
            updated_source.user = request.user
            if updated_source.source_type == "google_drive" and not updated_source.google_credentials:
                messages.error(request, "Google Drive source requires linked Google credentials")
            else:
                updated_source.save()
                messages.success(request, "Source updated")
                return redirect("source_list")
    else:
        form = DocumentSourceForm(instance=source, user=request.user)

    return render(
        request,
        "accounts/source_form.html",
        {"form": form, "create": False, "source": source, "selected_tab": "sources"},
    )


@login_required
@require_http_methods(["POST"])
def source_delete_view(request: TypedHttpRequest, pk: int) -> HttpResponse:
    source = get_object_or_404(DocumentSource, pk=pk, user=request.user)
    source.delete()
    messages.success(request, "Source deleted")
    return redirect("source_list")
