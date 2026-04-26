"""
project_extractor.py
--------------------
Drop this file into any project folder and run it.
It will generate:
  - structure_1.txt, structure_2.txt, ... → full folder/file tree
  - content_1.txt, content_2.txt, ...     → every line of every file with filenames
Each output file is capped at 100MB.
"""

import os
import sys

# ─── CONFIG ───────────────────────────────────────────────────────────────────
MAX_BYTES = 100 * 1024 * 1024  # 100 MB per output file

# Files / folders to skip entirely
SKIP_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules",
    ".venv", "venv", "env", ".env", "dist", "build",
    ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
}
SKIP_FILES = {
    "project_extractor.py",   # skip itself
}
SKIP_EXTENSIONS = {
    # binaries & media – unreadable as text
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".mp4", ".mp3", ".wav", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".pyc", ".pyo", ".class",
    ".db", ".sqlite", ".sqlite3",
    ".lock",   # package lock files (huge, not useful)
}
# ──────────────────────────────────────────────────────────────────────────────


def should_skip(path: str, is_dir: bool) -> bool:
    name = os.path.basename(path)
    if is_dir:
        return name in SKIP_DIRS
    if name in SKIP_FILES:
        return True
    _, ext = os.path.splitext(name)
    return ext.lower() in SKIP_EXTENSIONS


def collect_all_files(root: str):
    """Walk the tree and return (relative_path, absolute_path) for every kept file."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped dirs in-place so os.walk won't descend into them
        dirnames[:] = sorted(
            d for d in dirnames
            if not should_skip(os.path.join(dirpath, d), is_dir=True)
        )
        rel_dir = os.path.relpath(dirpath, root)
        for fname in sorted(filenames):
            abs_path = os.path.join(dirpath, fname)
            if should_skip(abs_path, is_dir=False):
                continue
            rel_path = os.path.join(rel_dir, fname) if rel_dir != "." else fname
            result.append((rel_path, abs_path))
    return result


def build_tree(root: str) -> str:
    """Return a pretty directory tree as a string."""
    lines = [os.path.basename(os.path.abspath(root)) + "/"]

    def _walk(current_dir: str, prefix: str):
        try:
            entries = sorted(os.listdir(current_dir))
        except PermissionError:
            return
        kept = []
        for e in entries:
            full = os.path.join(current_dir, e)
            if os.path.isdir(full):
                if not should_skip(full, is_dir=True):
                    kept.append((e, True))
            else:
                if not should_skip(full, is_dir=False):
                    kept.append((e, False))

        for i, (name, is_dir) in enumerate(kept):
            connector = "└── " if i == len(kept) - 1 else "├── "
            lines.append(prefix + connector + name + ("/" if is_dir else ""))
            if is_dir:
                extension = "    " if i == len(kept) - 1 else "│   "
                _walk(os.path.join(current_dir, name), prefix + extension)

    _walk(root, "")
    return "\n".join(lines)


def write_chunked(output_dir: str, base_name: str, chunks: list[str]):
    """Write a list of string chunks into numbered files, each ≤ MAX_BYTES."""
    file_index = 1
    current_path = os.path.join(output_dir, f"{base_name}_{file_index}.txt")
    current_size = 0
    current_file = open(current_path, "w", encoding="utf-8", errors="replace")
    print(f"  Writing {os.path.basename(current_path)} ...")

    for chunk in chunks:
        encoded = chunk.encode("utf-8", errors="replace")
        if current_size + len(encoded) > MAX_BYTES:
            current_file.close()
            file_index += 1
            current_path = os.path.join(output_dir, f"{base_name}_{file_index}.txt")
            current_file = open(current_path, "w", encoding="utf-8", errors="replace")
            current_size = 0
            print(f"  Writing {os.path.basename(current_path)} ...")
        current_file.write(chunk)
        current_size += len(encoded)

    current_file.close()


def main():
    # Run from any folder; script's own folder is the project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root = script_dir
    output_dir = os.path.join(root, "project_extraction_output")
    os.makedirs(output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Project Extractor")
    print(f"  Root : {root}")
    print(f"  Output: {output_dir}")
    print(f"{'='*60}\n")

    # ── STRUCTURE FILES ──────────────────────────────────────────
    print("Building directory tree ...")
    tree_str = build_tree(root)

    all_files = collect_all_files(root)
    file_list_str = "\n".join(f"  {rp}" for rp, _ in all_files)

    structure_chunks = [
        "=" * 60 + "\n",
        "PROJECT STRUCTURE TREE\n",
        "=" * 60 + "\n\n",
        tree_str + "\n\n",
        "=" * 60 + "\n",
        "ALL INCLUDED FILES\n",
        "=" * 60 + "\n\n",
        file_list_str + "\n",
    ]

    print("Writing structure files ...")
    write_chunked(output_dir, "structure", structure_chunks)

    # ── CONTENT FILES ────────────────────────────────────────────
    print("\nWriting content files ...")
    content_chunks = []
    total_files = len(all_files)

    for idx, (rel_path, abs_path) in enumerate(all_files, 1):
        header = (
            "\n" + "=" * 60 + "\n"
            f"FILE ({idx}/{total_files}): {rel_path}\n"
            + "=" * 60 + "\n"
        )
        content_chunks.append(header)

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for lineno, line in enumerate(lines, 1):
                content_chunks.append(f"{lineno:>6} | {line}")
            content_chunks.append("\n")
        except Exception as e:
            content_chunks.append(f"  [Could not read file: {e}]\n")

    write_chunked(output_dir, "content", content_chunks)

    # ── SUMMARY ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Done.")
    print(f"  Total files processed : {total_files}")
    output_files = sorted(os.listdir(output_dir))
    print(f"  Output files generated: {len(output_files)}")
    for f in output_files:
        size_mb = os.path.getsize(os.path.join(output_dir, f)) / (1024 * 1024)
        print(f"    {f}  ({size_mb:.2f} MB)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()