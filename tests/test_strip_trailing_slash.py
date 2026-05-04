from semgrep_ghes_util.cli import _strip_trailing_slash


def test_strips_trailing_slash():
    assert _strip_trailing_slash("https://example.com/") == "https://example.com"


def test_no_trailing_slash_unchanged():
    assert _strip_trailing_slash("https://example.com") == "https://example.com"


def test_strips_multiple_trailing_slashes():
    assert _strip_trailing_slash("https://example.com///") == "https://example.com"


def test_none_passthrough():
    assert _strip_trailing_slash(None) is None


def test_preserves_path_segment():
    # A user might point at a subpath (e.g., a GHES instance hosted under a prefix).
    # We only strip the final slash, not the path itself.
    assert _strip_trailing_slash("https://example.com/api/v3") == "https://example.com/api/v3"


def test_strips_trailing_slash_after_path_segment():
    assert _strip_trailing_slash("https://example.com/api/v3/") == "https://example.com/api/v3"


def test_strips_trailing_slash_with_nested_path():
    assert (
        _strip_trailing_slash("https://gitlab.example.com/group/subgroup/")
        == "https://gitlab.example.com/group/subgroup"
    )
