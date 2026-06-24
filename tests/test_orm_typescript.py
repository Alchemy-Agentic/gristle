"""Tests for TypeORM entity detection."""

from __future__ import annotations

from gristle.parsers.orm_typescript import extract_typeorm_models

_ENTITY = """
@Entity('users')
export class User {
  @PrimaryGeneratedColumn()
  id: number;
  @Column({ unique: true })
  email: string;
  @ManyToOne(() => Org)
  org: Org;
}
"""


def test_typeorm_entity_fields_and_relations():
    models = extract_typeorm_models("user.entity.ts", _ENTITY)
    user = next(m for m in models if m.name == "User")
    assert user.orm == "typeorm"
    assert user.table_name == "users"
    fields = {f.name: f for f in user.fields}
    assert fields["id"].is_primary_key
    assert fields["email"].is_unique
    assert "org" not in fields  # relation, not a column field
    assert any(r.target_model == "Org" and r.relation_type == "many-to-one" for r in user.relations)


def test_non_entity_class_ignored():
    assert extract_typeorm_models("svc.ts", "export class Service {\n  do() {}\n}\n") == []


def test_no_entity_marker_skips_parse():
    assert extract_typeorm_models("a.ts", "export const x = 1;\n") == []
