"""Focused checks for content-based session-to-commit evidence."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.link_mutations_to_git import _patch_hunks, _patch_witness, _transition


def main() -> None:
    before = "header\nold setting\nfooter\n"
    after = "header\nnew setting\nfooter\n"
    patch = "@@ -1,3 +1,3 @@\n header\n-old setting\n+new setting\n footer\n"
    assert _patch_hunks(patch) == [
        ("header\nold setting\nfooter", "header\nnew setting\nfooter")]
    witness = _patch_witness(patch, before, after)
    assert witness and witness[0]["old_offset"] == 0
    assert witness[0]["new_offset"] == 0
    assert _patch_witness(patch, before, before) is None
    assert _patch_witness(patch, "different\n", after) is None
    assert _transition("old setting", "new setting", before, after, "Edit")
    assert not _transition("old setting", "new setting", after, after, "Edit")
    insertion = "@@ -1 +1,2 @@\n header\n+added configuration line\n"
    assert _patch_witness(insertion, "header\n",
                          "header\nadded configuration line\n")
    print("content witness: PASS")


if __name__ == "__main__":
    main()
