from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignUpForm(UserCreationForm):
    """UserCreationForm bound to the project's user model.

    Password hashing, confirmation and the configured password validators all come from
    Django; nothing about credentials is hand-rolled here.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name")
