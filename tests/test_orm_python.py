"""Tests for SQLAlchemy/Django ORM model detection."""

from __future__ import annotations

from gristle.parsers.orm_python import extract_python_orm_models

_SQLALCHEMY = """
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Column, String, ForeignKey

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email = Column(String, unique=True)
    org_id = Column(ForeignKey("orgs.id"))
    posts = relationship("Post")
"""

_DJANGO = """
from django.db import models

class Article(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey("User", on_delete=models.CASCADE)
"""


def test_sqlalchemy_model_fields():
    models = extract_python_orm_models("models.py", _SQLALCHEMY)
    by_name = {m.name: m for m in models}
    assert "Base" not in by_name  # no table/columns -> not a model
    user = by_name["User"]
    assert user.orm == "sqlalchemy"
    assert user.table_name == "users"
    fields = {f.name: f for f in user.fields}
    assert fields["id"].is_primary_key
    assert fields["email"].is_unique
    assert fields["org_id"].is_foreign_key
    assert fields["org_id"].references_model == "orgs"
    assert fields["org_id"].references_field == "id"
    assert any(r.target_model == "Post" for r in user.relations)


def test_django_model_fields_and_relations():
    models = extract_python_orm_models("models.py", _DJANGO)
    article = next(m for m in models if m.name == "Article")
    assert article.orm == "django"
    fields = {f.name: f for f in article.fields}
    assert fields["title"].field_type == "CharField"
    assert fields["author"].is_foreign_key
    assert fields["author"].references_model == "User"
    assert any(r.target_model == "User" and r.relation_type == "many-to-one" for r in article.relations)


def test_non_orm_class_is_ignored():
    assert extract_python_orm_models("a.py", "class Plain:\n    x = 1\n") == []


def test_no_orm_hints_skips_parsing():
    assert extract_python_orm_models("a.py", "def f():\n    return 1\n") == []
