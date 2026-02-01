"""Quick check of markdown code reference patterns in pig-knuckle."""
import sys
import re
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# Check DATA_MODEL.md
content = Path(r"d:\projects\pig-knuckle\DATA_MODEL.md").read_text(encoding="utf-8", errors="replace")
lines = content.splitlines()

# Extract backtick code references
backtick_refs = re.findall(r"`([^`]+)`", content)
print(f"DATA_MODEL.md: {len(lines)} lines, {len(backtick_refs)} backtick refs")
# Show unique refs that look like code entities
code_like = [r for r in backtick_refs if re.match(r"^[A-Za-z_]\w*(\.\w+)*$", r) and len(r) > 2]
print(f"  Code-like refs: {len(code_like)}")
for r in sorted(set(code_like))[:20]:
    print(f"    {r}")

print()

# Check ARCHITECTURE.md
content2 = Path(r"d:\projects\pig-knuckle\docs\architecture\ARCHITECTURE.md").read_text(encoding="utf-8", errors="replace")
lines2 = content2.splitlines()
backtick_refs2 = re.findall(r"`([^`]+)`", content2)
print(f"ARCHITECTURE.md: {len(lines2)} lines, {len(backtick_refs2)} backtick refs")
code_like2 = [r for r in backtick_refs2 if re.match(r"^[A-Za-z_]\w*(\.\w+)*$", r) and len(r) > 2]
print(f"  Code-like refs: {len(code_like2)}")
for r in sorted(set(code_like2))[:20]:
    print(f"    {r}")

print()

# Check for file path references across all docs
all_md = list(Path(r"d:\projects\pig-knuckle").rglob("*.md"))
all_md = [f for f in all_md if "node_modules" not in str(f)]
total_file_refs = 0
total_backtick_refs = 0
for f in all_md:
    try:
        c = f.read_text(encoding="utf-8", errors="replace")
        total_backtick_refs += len(re.findall(r"`[^`]+`", c))
        # File path patterns like src/foo/bar.ts or ./foo/bar
        total_file_refs += len(re.findall(r"(?:src|\.)/[\w/.-]+\.\w{1,4}", c))
    except Exception:
        pass

print(f"Across all {len(all_md)} markdown files:")
print(f"  Total backtick code spans: {total_backtick_refs}")
print(f"  Total file path references: {total_file_refs}")
