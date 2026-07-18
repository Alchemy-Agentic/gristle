"""Tests for the Supabase generated-types parser."""

from __future__ import annotations

from gristle.parsers.supabase import (
    is_supabase_types,
    parse_supabase_db_functions,
    parse_supabase_types,
)

# A trimmed `supabase gen types typescript` output: two tables (one with an FK
# and an enum-typed column), a view, and the empty graphql_public schema.
GENERATED = """\
export type Json = string | number | boolean | null

export type Database = {
  __InternalSupabase: {
    PostgrestVersion: "14.4"
  }
  graphql_public: {
    Tables: {
      [_ in never]: never
    }
    Views: {
      [_ in never]: never
    }
  }
  public: {
    Tables: {
      users: {
        Row: {
          id: string
          email: string
          created_at: string | null
        }
        Insert: {
          id?: string
          email: string
        }
        Update: {
          email?: string
        }
        Relationships: []
      }
      executions: {
        Row: {
          id: string
          user_id: string
          status: Database["public"]["Enums"]["run_status"] | null
          tokens: number | null
        }
        Insert: {
          id?: string
          user_id: string
        }
        Update: {
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "executions_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "users"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      execution_stats: {
        Row: {
          user_id: string | null
          total: number | null
        }
        Relationships: []
      }
    }
    Functions: {
      get_stats: {
        Args: { uid: string }
        Returns: Json
      }
    }
    Enums: {
      run_status: "pending" | "done"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}
"""


class TestDetection:
    def test_detects_generated_types(self):
        assert is_supabase_types(GENERATED)

    def test_rejects_ordinary_typescript(self):
        assert not is_supabase_types("export const x = 1;\n")

    def test_rejects_drizzle_schema(self):
        code = "import { pgTable } from 'drizzle-orm/pg-core';\nexport const users = pgTable('users', {});\n"
        assert not is_supabase_types(code)

    def test_sniff_pass_without_structure_yields_no_models(self):
        """A false-positive sniff is harmless: no Database structure, no models."""
        code = "// Database Tables: Row: nothing real here\nexport const Database = 1;\n"
        assert parse_supabase_types("x.ts", code) == []


class TestDBFunctions:
    def test_parses_functions_block(self):
        fns = parse_supabase_db_functions("src/types/database.types.ts", GENERATED)
        assert len(fns) == 1
        fn = fns[0]
        assert fn.name == "get_stats"
        assert fn.args == ["uid"]
        assert fn.returns == "Json"
        assert fn.schema == "public"
        assert fn.qualified_name == "src/types/database.types.ts::public.get_stats"

    def test_multi_arg_and_no_arg_and_overload(self):
        code = """\
export type Database = {
  public: {
    Tables: { t: { Row: { id: string }, Relationships: [] } }
    Functions: {
      deduct_credits: {
        Args: { p_user_id: string; p_amount: number }
        Returns: boolean
      }
      housekeeping: {
        Args: Record<string, never>
        Returns: undefined
      }
      overloaded: {
        Args: { a: string } | { b: number }
        Returns: Json
      }
    }
  }
}
"""
        fns = {f.name: f for f in parse_supabase_db_functions("db.ts", code)}
        assert fns["deduct_credits"].args == ["p_user_id", "p_amount"]
        assert fns["housekeeping"].args == []  # Record<string, never> -> no params
        assert fns["overloaded"].args == ["a"]  # first overload branch

    def test_no_functions_block(self):
        code = "export type Database = { public: { Tables: { t: { Row: { id: string } } } } }\n"
        assert parse_supabase_db_functions("db.ts", code) == []

    def test_overloaded_function_takes_first_branch(self):
        """Overloaded/polymorphic functions are a union at the value level; the
        first branch's signature is used (not empty). Leading-pipe unions nest, so
        a shallow scan would wrongly take the second branch."""
        code = """\
export type Database = {
  public: {
    Tables: { t: { Row: { id: string }, Relationships: [] } }
    Functions: {
      get_messages:
        | {
            Args: { channel_row: number }
            Returns: string[]
          }
        | {
            Args: { other_row: string }
            Returns: number
          }
    }
  }
}
"""
        fns = parse_supabase_db_functions("db.ts", code)
        assert len(fns) == 1
        assert fns[0].name == "get_messages"
        assert fns[0].args == ["channel_row"]  # first branch, not "other_row"
        assert fns[0].returns == "string[]"

    def test_returns_enum_ref_single_quotes(self):
        """Codegen may emit enum refs with single quotes; normalize to the name."""
        code = """\
export type Database = {
  public: {
    Tables: { t: { Row: { id: string }, Relationships: [] } }
    Functions: {
      current_status: {
        Args: { p_id: string }
        Returns: Database['public']['Enums']['user_status']
      }
    }
  }
}
"""
        fns = parse_supabase_db_functions("db.ts", code)
        assert fns[0].returns == "user_status"


class TestParsing:
    def test_tables_and_views_become_models(self):
        models = parse_supabase_types("src/types/database.types.ts", GENERATED)
        by_name = {m.name: m for m in models}
        assert set(by_name) == {"users", "executions", "execution_stats"}
        assert all(m.orm == "supabase" for m in models)
        # Table name is exact — never pluralize-inferred.
        assert by_name["executions"].table_name == "executions"
        # Views are marked so consumers can tell them apart.
        assert by_name["execution_stats"].docstring == "Supabase view"
        assert by_name["users"].docstring is None

    def test_row_fields_with_nullability(self):
        models = parse_supabase_types("db.ts", GENERATED)
        users = next(m for m in models if m.name == "users")
        fields = {f.name: f for f in users.fields}
        assert set(fields) == {"id", "email", "created_at"}
        assert not fields["id"].is_nullable
        assert fields["created_at"].is_nullable
        assert fields["created_at"].field_type == "string"  # `| null` stripped

    def test_enum_references_are_normalized(self):
        models = parse_supabase_types("db.ts", GENERATED)
        executions = next(m for m in models if m.name == "executions")
        status = next(f for f in executions.fields if f.name == "status")
        assert status.field_type == "run_status"
        assert status.is_nullable

    def test_foreign_keys_from_relationships(self):
        models = parse_supabase_types("db.ts", GENERATED)
        executions = next(m for m in models if m.name == "executions")
        user_id = next(f for f in executions.fields if f.name == "user_id")
        assert user_id.is_foreign_key
        assert user_id.references_model == "users"
        assert user_id.references_field == "id"
        assert len(executions.relations) == 1
        rel = executions.relations[0]
        assert rel.target_model == "users"
        assert rel.relation_type == "many-to-one"
        assert rel.foreign_key_field == "user_id"
        assert rel.orm_hint == "supabase_fk"

    def test_empty_schema_contributes_nothing(self):
        """graphql_public's `[_ in never]: never` mapped types yield no models."""
        models = parse_supabase_types("db.ts", GENERATED)
        assert all(m.qualified_name.startswith("db.ts::public.") for m in models)

    def test_interface_style_database(self):
        """Older CLI versions emitted `export interface Database`."""
        code = """\
export interface Database {
  public: {
    Tables: {
      posts: {
        Row: {
          id: string
          title: string | null
        }
        Relationships: []
      }
    }
  }
}
"""
        models = parse_supabase_types("db.ts", code)
        assert len(models) == 1
        assert models[0].name == "posts"
        assert {f.name for f in models[0].fields} == {"id", "title"}

    def test_one_to_one_relationship(self):
        code = """\
export type Database = {
  public: {
    Tables: {
      profiles: {
        Row: {
          id: string
          user_id: string
        }
        Relationships: [
          {
            foreignKeyName: "profiles_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: true
            referencedRelation: "users"
            referencedColumns: ["id"]
          },
        ]
      }
    }
  }
}
"""
        models = parse_supabase_types("db.ts", code)
        assert models[0].relations[0].relation_type == "one-to-one"
