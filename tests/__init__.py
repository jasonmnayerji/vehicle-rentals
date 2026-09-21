import pytest

# The behavioural suite in tests/desk_contract.py is shared by the unit and integration
# tests. It is not a test_*.py file, so pytest would not rewrite its asserts (and failures
# would show no values) unless told to before it is first imported.
pytest.register_assert_rewrite("tests.desk_contract")
