from __future__ import annotations

import logging

from django.contrib.auth import login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import SignUpForm

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def signup(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        logger.info("account created", extra={"user_id": user.pk})
        return redirect("dashboard")
    return render(request, "registration/signup.html", {"form": form})
