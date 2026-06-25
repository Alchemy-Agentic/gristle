"""Tests for the Python parser."""

from gristle.parsers.python import PythonParser


class TestImportExtraction:
    def test_extracts_regular_import(self):
        parser = PythonParser()
        result = parser.parse_file("test.py", "import os\nimport sys\n")
        assert len(result.imports) == 2
        assert result.imports[0].module_path == "os"
        assert result.imports[1].module_path == "sys"

    def test_extracts_from_import(self):
        parser = PythonParser()
        result = parser.parse_file("test.py", "from os.path import join, exists\n")
        assert len(result.imports) == 1
        assert result.imports[0].module_path == "os.path"

    def test_extracts_relative_import(self):
        parser = PythonParser()
        result = parser.parse_file("test.py", "from . import utils\n")
        assert len(result.imports) == 1
        assert result.imports[0].is_relative is True


class TestClassExtraction:
    def test_extracts_class(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        class_names = [c.name for c in result.classes]
        assert "UserService" in class_names
        assert "OrderService" in class_names

    def test_extracts_class_bases(self, sample_services_code):
        parser = PythonParser()
        # Test with explicit base class
        code = "class Foo(Bar, Baz):\n    pass\n"
        result = parser.parse_file("test.py", code)
        assert result.classes[0].bases == ["Bar", "Baz"]

    def test_extracts_class_docstring(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        user_svc = next(c for c in result.classes if c.name == "UserService")
        assert user_svc.docstring == "Service for user management operations."

    def test_extracts_methods(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        user_svc = next(c for c in result.classes if c.name == "UserService")
        method_names = [m.name for m in user_svc.methods]
        assert "__init__" in method_names
        assert "get_user" in method_names
        assert "create_user" in method_names
        assert "deactivate_user" in method_names

    def test_detects_async_methods(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        user_svc = next(c for c in result.classes if c.name == "UserService")
        deactivate = next(m for m in user_svc.methods if m.name == "deactivate_user")
        assert deactivate.is_async is True


class TestFunctionExtraction:
    def test_extracts_module_functions(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        func_names = [f.name for f in result.functions]
        assert "validate_email" in func_names
        assert "notify_order_created" in func_names

    def test_does_not_include_methods_as_module_functions(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        func_names = [f.name for f in result.functions]
        assert "get_user" not in func_names
        assert "__init__" not in func_names

    def test_extracts_function_docstring(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        validate = next(f for f in result.functions if f.name == "validate_email")
        assert validate.docstring == "Validate that an email address is properly formatted."

    def test_extracts_return_type(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        validate = next(f for f in result.functions if f.name == "validate_email")
        assert validate.return_type == "bool"


class TestCallExtraction:
    def test_extracts_function_calls(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        user_svc = next(c for c in result.classes if c.name == "UserService")
        create = next(m for m in user_svc.methods if m.name == "create_user")
        assert "validate_email" in create.calls

    def test_extracts_method_calls_on_self(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        user_svc = next(c for c in result.classes if c.name == "UserService")
        deactivate = next(m for m in user_svc.methods if m.name == "deactivate_user")
        # self.get_user -> UserService.get_user
        assert "UserService.get_user" in deactivate.calls

    def test_extracts_cross_service_calls(self, sample_services_code):
        parser = PythonParser()
        result = parser.parse_file("services.py", sample_services_code)
        order_svc = next(c for c in result.classes if c.name == "OrderService")
        create_order = next(m for m in order_svc.methods if m.name == "create_order")
        # Should capture the call to notify_order_created
        assert "notify_order_created" in create_order.calls

    def test_captures_positional_arg_identifiers(self):
        """calls_with_args records the model/table passed as an argument so the
        schema linker can see it (the callee name in `calls` drops args)."""
        parser = PythonParser()
        code = (
            "def load(session):\n"
            "    return session.query(User).filter_by(id=1).first()\n"
            "\n"
            "def add(session, obj):\n"
            "    session.add(obj)\n"
        )
        result = parser.parse_file("repo.py", code)
        load = next(f for f in result.functions if f.name == "load")
        assert "session.query(User)" in load.calls_with_args
        # filter_by(id=1) is keyword-only -> not captured as a positional ident
        assert all("filter_by(" not in c for c in load.calls_with_args)
        # The plain callee name is still in `calls`, args dropped.
        assert "session.query" in load.calls


class TestVariableExtraction:
    def test_extracts_module_assignments(self):
        parser = PythonParser()
        result = parser.parse_file("settings.py", "SETTINGS = {'debug': True}\napp = FastAPI()\nrouter = APIRouter()\n")
        by = {v.name: v for v in result.variables}
        assert by["SETTINGS"].value_kind == "object"
        assert by["SETTINGS"].is_exported is True  # public, non-underscore
        assert by["app"].value_kind == "call"
        assert by["router"].value_kind == "call"

    def test_function_is_not_a_variable(self):
        parser = PythonParser()
        result = parser.parse_file("m.py", "def f():\n    return 1\n\nMAX = 5\n")
        names = {v.name for v in result.variables}
        assert "f" not in names
        assert "MAX" in names

    def test_underscore_name_not_exported(self):
        parser = PythonParser()
        result = parser.parse_file("m.py", "_private = 1\n")
        v = next(v for v in result.variables if v.name == "_private")
        assert v.is_exported is False


class TestErrorFlow:
    def test_raises_and_catches(self):
        parser = PythonParser()
        code = "def f():\n    try:\n        do()\n    except KeyError:\n        raise ValueError('bad')\n"
        fn = parser.parse_file("m.py", code).functions[0]
        assert "ValueError" in fn.raises
        assert "KeyError" in fn.catches

    def test_attribute_exception_and_tuple_catch(self):
        parser = PythonParser()
        code = "def f():\n    try:\n        pass\n    except (TypeError, errors.NotFound):\n        raise mod.CustomError()\n"
        fn = parser.parse_file("m.py", code).functions[0]
        assert "CustomError" in fn.raises  # attribute -> last segment
        assert "TypeError" in fn.catches
        assert "NotFound" in fn.catches

    def test_bare_raise_and_alias_not_captured(self):
        parser = PythonParser()
        code = "def f():\n    try:\n        pass\n    except Exception as e:\n        raise\n"
        fn = parser.parse_file("m.py", code).functions[0]
        assert fn.raises == []  # bare re-raise has no type
        assert fn.catches == ["Exception"]  # the `as e` alias is not captured

    def test_has_error_handling(self):
        parser = PythonParser()
        with_try = "def f():\n    try:\n        do()\n    except Exception:\n        pass\n"
        without = "def g():\n    do()\n"
        # try/finally with no named exception still counts (catches stays empty)
        finally_only = "def h():\n    try:\n        do()\n    finally:\n        cleanup()\n"
        assert parser.parse_file("m.py", with_try).functions[0].has_error_handling is True
        assert parser.parse_file("m.py", without).functions[0].has_error_handling is False
        h = parser.parse_file("m.py", finally_only).functions[0]
        assert h.has_error_handling is True
        assert h.catches == []  # no named exception, but error handling is present


class TestPermissionClasses:
    def test_tuple_permission_classes(self):
        parser = PythonParser()
        code = (
            "class ArticleViewSet(viewsets.ModelViewSet):\n"
            "    permission_classes = (IsAuthenticatedOrReadOnly,)\n"
            "    def list(self): pass\n"
        )
        cls = parser.parse_file("views.py", code).classes[0]
        assert cls.permission_classes == ["IsAuthenticatedOrReadOnly"]

    def test_list_and_dotted_permission_classes(self):
        parser = PythonParser()
        code = "class ProfileView(APIView):\n    permission_classes = [permissions.IsAuthenticated, AllowAny]\n"
        cls = parser.parse_file("views.py", code).classes[0]
        assert cls.permission_classes == ["IsAuthenticated", "AllowAny"]

    def test_no_permission_classes(self):
        parser = PythonParser()
        cls = parser.parse_file("views.py", "class Plain:\n    x = 1\n").classes[0]
        assert cls.permission_classes == []


class TestDjangoRoutes:
    def test_path_with_class_based_view(self):
        parser = PythonParser()
        code = (
            "from django.urls import path\n"
            "from .views import ArticleList\n"
            "urlpatterns = [\n"
            "    path('articles/', ArticleList.as_view()),\n"
            "]\n"
        )
        result = parser.parse_file("urls.py", code)
        assert len(result.routes) == 1
        route = result.routes[0]
        assert route.method == "ALL"
        assert route.path == "/articles/"
        assert route.handler_name == "ArticleList"

    def test_re_path_named_groups_normalized(self):
        parser = PythonParser()
        code = (
            "from django.urls import re_path\n"
            "urlpatterns = [\n"
            "    re_path(r'^articles/(?P<slug>[-\\w]+)/$', ArticleDetail.as_view()),\n"
            "]\n"
        )
        result = parser.parse_file("urls.py", code)
        assert result.routes[0].path == "/articles/:slug/"
        assert result.routes[0].handler_name == "ArticleDetail"

    def test_drf_router_register(self):
        parser = PythonParser()
        code = (
            "from rest_framework import routers\n"
            "router = routers.DefaultRouter()\n"
            "router.register(r'articles', ArticleViewSet)\n"
            "urlpatterns = router.urls\n"
        )
        result = parser.parse_file("urls.py", code)
        regs = [r for r in result.routes if r.handler_name == "ArticleViewSet"]
        assert len(regs) == 1
        assert regs[0].path == "/articles"

    def test_include_and_admin_mounts_skipped(self):
        parser = PythonParser()
        code = (
            "from django.urls import path, include\n"
            "from django.contrib import admin\n"
            "urlpatterns = [\n"
            "    path('admin/', admin.site.urls),\n"
            "    path('api/', include('api.urls')),\n"
            "]\n"
        )
        result = parser.parse_file("urls.py", code)
        assert result.routes == []  # both are mounts, not leaf routes

    def test_no_django_routes_without_urlpatterns(self):
        parser = PythonParser()
        # path() from pathlib must not be mistaken for a Django route
        code = "from pathlib import Path\nfor p in path('.'):\n    print(p)\n"
        result = parser.parse_file("helper.py", code)
        assert result.routes == []


class TestDecoratorExtraction:
    def test_extracts_decorators(self):
        parser = PythonParser()
        code = "@staticmethod\ndef foo():\n    pass\n"
        result = parser.parse_file("test.py", code)
        assert result.functions[0].decorators == ["staticmethod"]
        assert result.functions[0].is_static is True

    def test_extracts_property_decorator(self):
        parser = PythonParser()
        code = "class Foo:\n    @property\n    def bar(self):\n        return 1\n"
        result = parser.parse_file("test.py", code)
        method = result.classes[0].methods[0]
        assert method.is_property is True


class TestDataclassExtraction:
    def test_extracts_dataclass(self, sample_models_code):
        parser = PythonParser()
        result = parser.parse_file("models.py", sample_models_code)
        assert len(result.classes) == 2
        assert result.classes[0].name == "User"
        assert result.classes[1].name == "Order"

    def test_extracts_module_docstring(self, sample_models_code):
        parser = PythonParser()
        result = parser.parse_file("models.py", sample_models_code)
        assert result.module_docstring == "Data models for the sample application."


class TestComplexity:
    def test_simple_function_complexity(self):
        parser = PythonParser()
        code = "def foo():\n    return 1\n"
        result = parser.parse_file("test.py", code)
        assert result.functions[0].complexity == 1

    def test_branching_increases_complexity(self):
        parser = PythonParser()
        code = (
            "def foo(x):\n"
            "    if x > 0:\n"
            "        return x\n"
            "    elif x < 0:\n"
            "        return -x\n"
            "    else:\n"
            "        return 0\n"
        )
        result = parser.parse_file("test.py", code)
        assert result.functions[0].complexity >= 3


class TestVisibility:
    def test_public_function(self):
        parser = PythonParser()
        result = parser.parse_file("t.py", "def foo(): pass\n")
        assert result.functions[0].visibility == "public"

    def test_protected_function(self):
        parser = PythonParser()
        result = parser.parse_file("t.py", "def _foo(): pass\n")
        assert result.functions[0].visibility == "protected"

    def test_private_function(self):
        parser = PythonParser()
        result = parser.parse_file("t.py", "def __foo(): pass\n")
        assert result.functions[0].visibility == "private"

    def test_dunder_is_public(self):
        parser = PythonParser()
        result = parser.parse_file("t.py", "class C:\n    def __init__(self): pass\n")
        assert result.classes[0].methods[0].visibility == "public"


class TestPythonTestCaseDetection:
    """Test that pytest test_* functions and Test* classes produce ParsedTestCase entries."""

    def test_module_level_test_functions(self):
        parser = PythonParser()
        code = (
            "def test_addition():\n"
            "    assert 1 + 1 == 2\n"
            "\n"
            "def test_subtraction():\n"
            "    assert 2 - 1 == 1\n"
            "\n"
            "def helper():\n"
            "    pass\n"
        )
        result = parser.parse_file("tests/test_math.py", code)
        assert len(result.test_cases) == 2
        names = [tc.name for tc in result.test_cases]
        assert "test_addition" in names
        assert "test_subtraction" in names
        # helper is not a test
        assert "helper" not in names
        for tc in result.test_cases:
            assert tc.block_type == "test"
            assert tc.parent_describe is None

    def test_test_class_with_methods(self):
        parser = PythonParser()
        code = (
            "class TestUserService:\n"
            "    def test_create_user(self):\n"
            "        pass\n"
            "\n"
            "    def test_delete_user(self):\n"
            "        pass\n"
            "\n"
            "    def helper(self):\n"
            "        pass\n"
        )
        result = parser.parse_file("tests/test_user.py", code)
        # class itself + 2 test methods = 3 test cases
        assert len(result.test_cases) == 3
        class_tc = next(tc for tc in result.test_cases if tc.block_type == "class")
        assert class_tc.name == "TestUserService"
        method_tcs = [tc for tc in result.test_cases if tc.block_type == "test"]
        assert len(method_tcs) == 2
        for tc in method_tcs:
            assert tc.parent_describe == "TestUserService"

    def test_non_test_file_with_test_funcs(self):
        """test_* functions should still be detected even outside test files."""
        parser = PythonParser()
        code = "def test_something():\n    pass\n"
        result = parser.parse_file("src/helpers.py", code)
        # is_test is set based on func name, test case should be emitted
        assert len(result.test_cases) == 1

    def test_no_test_cases_in_regular_code(self):
        parser = PythonParser()
        code = "def compute():\n    pass\n\nclass Service:\n    def run(self):\n        pass\n"
        result = parser.parse_file("src/service.py", code)
        assert len(result.test_cases) == 0


class TestNestedClassCapture:
    """Test that classes defined inside functions are captured."""

    def test_class_inside_function(self):
        parser = PythonParser()
        code = "def test_schema():\n    class UserSchema:\n        name = 'test'\n\n    s = UserSchema()\n"
        result = parser.parse_file("tests/test_schemas.py", code)
        class_names = [c.name for c in result.classes]
        assert "UserSchema" in class_names

    def test_multiple_nested_classes(self):
        parser = PythonParser()
        code = (
            "def test_models():\n"
            "    class FakeUser:\n"
            "        pass\n"
            "\n"
            "    class FakeOrder:\n"
            "        pass\n"
            "\n"
            "    assert FakeUser() is not None\n"
        )
        result = parser.parse_file("tests/test_models.py", code)
        class_names = [c.name for c in result.classes]
        assert "FakeUser" in class_names
        assert "FakeOrder" in class_names

    def test_no_nested_classes_in_regular_code(self):
        parser = PythonParser()
        code = "class TopLevel:\n    pass\n\ndef func():\n    x = 1\n"
        result = parser.parse_file("src/mod.py", code)
        assert len(result.classes) == 1
        assert result.classes[0].name == "TopLevel"


class TestParametrizeDetection:
    """Test that @pytest.mark.parametrize is detected and counted."""

    def test_parametrize_count_simple(self):
        parser = PythonParser()
        code = "import pytest\n\n@pytest.mark.parametrize('x', [1, 2, 3])\ndef test_values(x):\n    assert x > 0\n"
        result = parser.parse_file("tests/test_p.py", code)
        assert len(result.test_cases) == 1
        assert result.test_cases[0].parametrize_count == 3

    def test_parametrize_tuples(self):
        parser = PythonParser()
        code = (
            "import pytest\n"
            "\n"
            "@pytest.mark.parametrize('a,b', [(1, 2), (3, 4)])\n"
            "def test_pairs(a, b):\n"
            "    assert a < b\n"
        )
        result = parser.parse_file("tests/test_p.py", code)
        assert result.test_cases[0].parametrize_count == 2

    def test_non_parametrized_has_zero_count(self):
        parser = PythonParser()
        code = "def test_simple():\n    pass\n"
        result = parser.parse_file("tests/test_s.py", code)
        assert result.test_cases[0].parametrize_count == 0

    def test_parametrize_in_class_method(self):
        parser = PythonParser()
        code = (
            "import pytest\n"
            "\n"
            "class TestMath:\n"
            "    @pytest.mark.parametrize('n', [1, 2, 3, 4])\n"
            "    def test_positive(self, n):\n"
            "        assert n > 0\n"
        )
        result = parser.parse_file("tests/test_m.py", code)
        method_tc = next(tc for tc in result.test_cases if tc.block_type == "test")
        assert method_tc.parametrize_count == 4


class TestFixtureDetection:
    """Test that @pytest.fixture is detected on functions."""

    def test_fixture_detected(self):
        parser = PythonParser()
        code = "import pytest\n\n@pytest.fixture\ndef client():\n    return TestClient()\n"
        result = parser.parse_file("tests/conftest.py", code)
        assert len(result.functions) == 1
        assert result.functions[0].is_fixture is True

    def test_fixture_with_scope(self):
        parser = PythonParser()
        code = "import pytest\n\n@pytest.fixture(scope='session')\ndef db():\n    return Database()\n"
        result = parser.parse_file("tests/conftest.py", code)
        assert result.functions[0].is_fixture is True

    def test_non_fixture_not_flagged(self):
        parser = PythonParser()
        code = "def helper():\n    pass\n"
        result = parser.parse_file("tests/conftest.py", code)
        assert result.functions[0].is_fixture is False


class TestParameterExtraction:
    """Test that function parameter names are extracted."""

    def test_simple_params(self):
        parser = PythonParser()
        code = "def foo(a, b, c):\n    pass\n"
        result = parser.parse_file("t.py", code)
        assert result.functions[0].parameters == ["a", "b", "c"]

    def test_self_excluded(self):
        parser = PythonParser()
        code = "class C:\n    def method(self, x):\n        pass\n"
        result = parser.parse_file("t.py", code)
        assert result.classes[0].methods[0].parameters == ["x"]

    def test_typed_params(self):
        parser = PythonParser()
        code = "def foo(a: int, b: str = 'x'):\n    pass\n"
        result = parser.parse_file("t.py", code)
        assert result.functions[0].parameters == ["a", "b"]

    def test_no_params(self):
        parser = PythonParser()
        code = "def foo():\n    pass\n"
        result = parser.parse_file("t.py", code)
        assert result.functions[0].parameters == []


class TestRouteExtraction:
    """Route decorators should extract the actual path argument."""

    def test_fastapi_get_route_path(self):
        parser = PythonParser()
        code = '@app.get("/users")\ndef get_users():\n    return []\n'
        result = parser.parse_file("routes.py", code)
        assert len(result.routes) == 1
        assert result.routes[0].method == "GET"
        assert result.routes[0].path == "/users"
        assert result.routes[0].handler_name == "get_users"

    def test_fastapi_post_route_path(self):
        parser = PythonParser()
        code = '@app.post("/items")\ndef create_item():\n    pass\n'
        result = parser.parse_file("routes.py", code)
        assert result.routes[0].path == "/items"
        assert result.routes[0].method == "POST"

    def test_flask_route_decorator(self):
        parser = PythonParser()
        code = '@app.route("/users", methods=["GET"])\ndef users():\n    pass\n'
        result = parser.parse_file("routes.py", code)
        assert result.routes[0].path == "/users"
        assert result.routes[0].method == "ALL"

    def test_router_post_with_path_param(self):
        parser = PythonParser()
        code = '@router.post("/items/{item_id}")\ndef update_item():\n    pass\n'
        result = parser.parse_file("routes.py", code)
        assert result.routes[0].path == "/items/{item_id}"

    def test_bare_decorator_falls_back_to_func_name(self):
        parser = PythonParser()
        code = "@app.get\ndef get_users():\n    pass\n"
        result = parser.parse_file("routes.py", code)
        assert result.routes[0].path == "/get_users"

    def test_empty_args_falls_back_to_func_name(self):
        parser = PythonParser()
        code = "@app.get()\ndef get_users():\n    pass\n"
        result = parser.parse_file("routes.py", code)
        assert result.routes[0].path == "/get_users"

    def test_fixture_with_args_still_detected(self):
        parser = PythonParser()
        code = "import pytest\n\n@pytest.fixture(scope='session')\ndef db():\n    return 'db'\n"
        result = parser.parse_file("conftest.py", code)
        assert result.functions[0].is_fixture is True


class TestEntryPointDetection:
    """Test framework-aware entry point detection with entry_point_reason."""

    def test_main_is_entry_point(self):
        parser = PythonParser()
        code = "def main():\n    print('hello')\n"
        result = parser.parse_file("app.py", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "main"

    def test_fixture_is_entry_point(self):
        parser = PythonParser()
        code = "import pytest\n\n@pytest.fixture\ndef client():\n    return TestClient()\n"
        result = parser.parse_file("tests/conftest.py", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "pytest_fixture"

    def test_fixture_with_scope_is_entry_point(self):
        parser = PythonParser()
        code = "import pytest\n\n@pytest.fixture(scope='session')\ndef db():\n    return Database()\n"
        result = parser.parse_file("tests/conftest.py", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "pytest_fixture"

    def test_init_is_entry_point(self):
        parser = PythonParser()
        code = "class Service:\n    def __init__(self):\n        pass\n"
        result = parser.parse_file("svc.py", code)
        init = result.classes[0].methods[0]
        assert init.is_entry_point is True
        assert init.entry_point_reason == "constructor"

    def test_django_view_is_entry_point(self):
        parser = PythonParser()
        code = "def index(request):\n    return HttpResponse('hello')\n"
        result = parser.parse_file("myapp/views.py", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "django_view"

    def test_django_view_private_not_entry_point(self):
        parser = PythonParser()
        code = "def _helper(request):\n    return None\n"
        result = parser.parse_file("myapp/views.py", code)
        assert result.functions[0].is_entry_point is False

    def test_click_command_is_entry_point(self):
        parser = PythonParser()
        code = "import click\n\n@click.command()\ndef serve():\n    pass\n"
        result = parser.parse_file("cli.py", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "cli_command"

    def test_typer_command_is_entry_point(self):
        parser = PythonParser()
        code = "import typer\n\n@app.command()\ndef run():\n    pass\n"
        result = parser.parse_file("cli.py", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "cli_command"

    def test_route_handler_is_entry_point(self):
        parser = PythonParser()
        code = '@app.get("/users")\ndef get_users():\n    return []\n'
        result = parser.parse_file("routes.py", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "route_handler"

    def test_regular_function_not_entry_point(self):
        parser = PythonParser()
        code = "def helper():\n    return 42\n"
        result = parser.parse_file("utils.py", code)
        assert result.functions[0].is_entry_point is False
        assert result.functions[0].entry_point_reason is None

    def test_non_views_file_not_django_view(self):
        parser = PythonParser()
        code = "def index():\n    pass\n"
        result = parser.parse_file("myapp/utils.py", code)
        assert result.functions[0].entry_point_reason != "django_view"


class TestPythonDunderAll:
    """Python __all__ declaration should mark functions/classes as exported."""

    def test_function_in_all_is_exported(self):
        parser = PythonParser()
        code = (
            '__all__ = ["foo", "bar"]\n\ndef foo():\n    pass\n\ndef bar():\n    pass\n\ndef _internal():\n    pass\n'
        )
        result = parser.parse_file("module.py", code)
        by_name = {f.name: f for f in result.functions}
        assert by_name["foo"].is_exported is True
        assert by_name["bar"].is_exported is True
        assert by_name["_internal"].is_exported is False

    def test_class_in_all_is_exported(self):
        parser = PythonParser()
        code = '__all__ = ["MyClass"]\n\nclass MyClass:\n    pass\n\nclass _Private:\n    pass\n'
        result = parser.parse_file("module.py", code)
        by_name = {c.name: c for c in result.classes}
        assert by_name["MyClass"].is_exported is True
        assert by_name["_Private"].is_exported is False

    def test_no_all_means_public_names_exported(self):
        """Without __all__, Python's convention is that public (non-underscore)
        module-level names are the public API, so they are exported."""
        parser = PythonParser()
        code = (
            "def foo():\n    pass\n\ndef _hidden():\n    pass\n\nclass Bar:\n    pass\n\nclass _Internal:\n    pass\n"
        )
        result = parser.parse_file("module.py", code)
        funcs = {f.name: f for f in result.functions}
        classes = {c.name: c for c in result.classes}
        assert funcs["foo"].is_exported is True
        assert funcs["_hidden"].is_exported is False
        assert classes["Bar"].is_exported is True
        assert classes["_Internal"].is_exported is False

    def test_tuple_style_all(self):
        parser = PythonParser()
        code = '__all__ = ("alpha", "beta")\n\ndef alpha():\n    pass\n\ndef beta():\n    pass\n'
        result = parser.parse_file("module.py", code)
        assert result.functions[0].is_exported is True
        assert result.functions[1].is_exported is True
