"""Ancestor-folder walk shared by the gallery and scene harvests.

Stash-sourced harvests see one image/scene path at a time; a name folder can sit several levels
above the file (`.../Blair Green (UKY)/P/instagram/img.jpg`). To attribute the file to the
subject folder regardless of nesting depth, we create a folder candidate for *every* ancestor
directory up to the harvest root — the same per-level coverage the filesystem path harvest has.
Triage then keeps the real name and discards the container levels (`Basketball`, `P`, …).
"""

import os


def ancestor_dirs(file_path: str, root: str | None) -> list[str]:
    """Directory paths from the file's immediate parent up to the folder just under `root`,
    nearest-first and bounded by `root` (exclusive).

    With no `root` (a whole-library sweep with no TOP_FOLDER), returns only the immediate
    parent — there is no subject root to climb toward. If `root` is set but is not an ancestor
    of the file, also falls back to the immediate parent, so we never walk to the filesystem
    root.
    """
    parent = os.path.dirname(file_path or "")
    if not parent:
        return []
    if not root:
        return [parent]
    root = root.rstrip("/")
    out: list[str] = []
    cur = parent
    while cur and cur != root and cur.startswith(root + "/"):
        out.append(cur)
        cur = os.path.dirname(cur)
    return out or [parent]
