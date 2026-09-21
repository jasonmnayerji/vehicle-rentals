from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Project user model.

    AbstractUser already provides everything this exercise needs: username, first and last
    name, email, password hashing, is_active / is_staff / is_superuser, groups and
    per-model permissions.

    It is subclassed anyway, with no extra fields, because Django cannot swap
    AUTH_USER_MODEL after the first migration without rebuilding every foreign key to it.
    Declaring it on day one costs nothing and keeps the option to add fields later.
    """
