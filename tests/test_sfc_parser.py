"""Tests for the single-file-component parser (Vue / Svelte / Astro).

The SFC parser locates the embedded script block and delegates to the TypeScript
parser, blanking non-script regions so reported line numbers are in the SFC file's
own coordinate space.
"""

from __future__ import annotations

from gristle.parsers.sfc import SFCParser


class TestVue:
    def test_script_setup_extracted_with_correct_lines(self):
        content = (
            "<template>\n"  # 1
            "  <div/>\n"  # 2
            "</template>\n"  # 3
            "\n"  # 4
            '<script setup lang="ts">\n'  # 5
            "import { ref } from 'vue'\n"  # 6
            "const count = ref(0)\n"  # 7
            "function increment() {\n"  # 8
            "  count.value++\n"  # 9
            "}\n"  # 10
            "</script>\n"  # 11
        )
        pf = SFCParser().parse_file("Counter.vue", content)
        assert pf.language == "vue"
        inc = next(f for f in pf.functions if f.name == "increment")
        assert inc.start_line == 8  # line maps to the .vue file, not the script offset
        assert inc.qualified_name == "Counter.vue::increment"
        assert any(v.name == "count" for v in pf.variables)
        assert any(imp.module_path == "vue" for imp in pf.imports)

    def test_multiple_script_blocks(self):
        content = (
            '<script lang="ts">\n'  # 1
            "import { defineComponent } from 'vue'\n"  # 2
            "</script>\n"  # 3
            '<script setup lang="ts">\n'  # 4
            "const ready = true\n"  # 5
            "</script>\n"  # 6
            "<template><div/></template>\n"  # 7
        )
        pf = SFCParser().parse_file("Both.vue", content)
        assert any(v.name == "ready" for v in pf.variables)
        assert any(imp.module_path == "vue" for imp in pf.imports)

    def test_template_only_no_crash(self):
        pf = SFCParser().parse_file("View.vue", "<template>\n  <div>hi</div>\n</template>\n")
        assert pf.language == "vue"
        assert pf.functions == []
        assert pf.variables == []


class TestSvelte:
    def test_script_extracted(self):
        content = (
            "<script>\n"  # 1
            "  import { onMount } from 'svelte'\n"  # 2
            "  function handle() { return 2 }\n"  # 3
            "</script>\n"  # 4
            "<button on:click={handle}>x</button>\n"  # 5
        )
        pf = SFCParser().parse_file("Btn.svelte", content)
        assert pf.language == "svelte"
        handle = next(f for f in pf.functions if f.name == "handle")
        assert handle.start_line == 3
        assert any(imp.module_path == "svelte" for imp in pf.imports)


class TestAstro:
    def test_frontmatter_extracted(self):
        content = (
            "---\n"  # 1
            "import Layout from '../Layout.astro'\n"  # 2
            "const title = 'Home'\n"  # 3
            "function greet() { return 1 }\n"  # 4
            "---\n"  # 5
            "<html><body>{title}</body></html>\n"  # 6
        )
        pf = SFCParser().parse_file("index.astro", content)
        assert pf.language == "astro"
        greet = next(f for f in pf.functions if f.name == "greet")
        assert greet.start_line == 4
        assert any(v.name == "title" for v in pf.variables)
        assert any("Layout" in imp.module_path for imp in pf.imports)

    def test_no_frontmatter(self):
        pf = SFCParser().parse_file("static.astro", "<html><body>plain</body></html>\n")
        assert pf.language == "astro"
        assert pf.functions == []


class TestMaskingIsolation:
    def test_template_text_not_parsed_as_code(self):
        # A `<template>` containing text that looks like code must not leak into the
        # parse — only the <script> block is real.
        content = (
            "<template>\n"
            "  function fake() {}\n"  # this is template text, NOT code
            "  import nope from 'x'\n"
            "</template>\n"
            "<script setup>\n"
            "const real = 1\n"
            "</script>\n"
        )
        pf = SFCParser().parse_file("Trap.vue", content)
        assert [f.name for f in pf.functions] == []  # 'fake' was in the template
        assert all(imp.module_path != "x" for imp in pf.imports)  # 'nope' was template
        assert any(v.name == "real" for v in pf.variables)
