from bridge.app.harvest.normalize import candidate, candidate_parts, tokenize


class TestNames:
    def test_simple_full_name(self):
        assert candidate("Jennifer Lawrence") == "Jennifer Lawrence"

    def test_separators_collapse(self):
        assert candidate("jennifer.lawrence.2015.1080p") == "jennifer lawrence"

    def test_mononym_survives(self):
        assert candidate("Cher") == "Cher"

    def test_non_latin_preserved(self):
        # Cyrillic must not be transliterated or dropped.
        assert candidate("Олександр Шевченко") == "Олександр Шевченко"

    def test_cjk_preserved(self):
        assert candidate("王伟") == "王伟"

    def test_embedded_name_kept_with_junk(self):
        # Exclusive strategy: we keep everything; the human triages the junk tokens.
        raw = "Jennifer Lawrence Interview WEB DL"
        assert candidate(raw) == raw

    def test_apostrophe_name_kept_whole(self):
        # Intra-name apostrophes must not split the surname (O'Dell -> O + Dell was a bug).
        assert candidate("Jennifer O'Dell") == "Jennifer O'Dell"
        assert candidate("D'Angelo Russell") == "D'Angelo Russell"

    def test_curly_apostrophe_normalized(self):
        assert candidate("Jennifer O’Dell") == "Jennifer O'Dell"


class TestGarbage:
    def test_pure_hash_mostly_dies(self):
        # A hex hash fragments into <2-char letter runs plus at most stray blobs.
        out = candidate("e8a8d6b7d27d0efeaffa1b832f04869a")
        # Whatever survives must not resemble the original 32-char blob.
        assert out is None or len(out) < 10

    def test_short_codes_survive_for_human_triage(self):
        # The gate does NOT classify: >=2-letter codes survive and are triaged by a human.
        # (This is the exclusive strategy — DESIGN §2.)
        assert candidate("IMG_0042") == "IMG"
        assert candidate("DSC 1234") == "DSC"

    def test_only_digits(self):
        assert candidate("2015 1080 264") is None

    def test_empty(self):
        assert candidate("") is None
        assert candidate("___.-.-") is None


class TestTokenize:
    def test_drops_single_chars(self):
        assert tokenize("J K Rowling") == ["Rowling"]

    def test_whitespace_and_punct(self):
        # Hyphens still separate (ambiguous with word-separator use); apostrophes join letters.
        assert tokenize("  Anne---Marie   O'Brien ") == ["Anne", "Marie", "O'Brien"]

    def test_trailing_apostrophe_not_joined(self):
        # An apostrophe with no following letter is just a separator (no dangling token).
        assert tokenize("Jennifer O' Dell") == ["Jennifer", "Dell"]


class TestCandidateParts:
    def test_trailing_paren_to_disambiguation(self):
        assert candidate_parts("Liz Gregorski (Wisconsin)") == ("Liz Gregorski", "Wisconsin")
        assert candidate_parts("Jane Doe (UVA)") == ("Jane Doe", "UVA")

    def test_no_paren_passthrough(self):
        assert candidate_parts("Jane Doe") == ("Jane Doe", "")

    def test_paren_only_yields_no_candidate(self):
        assert candidate_parts("(UVA)") is None

    def test_only_trailing_paren_is_peeled(self):
        # Mid-string parens stay in the name; only the trailing qualifier is peeled.
        assert candidate_parts("Jane (JJ) Doe (App State)") == ("Jane JJ Doe", "App State")

    def test_gate_still_applies_to_name(self):
        assert candidate_parts("0001 (Alabama)") is None  # digits-only name gated out
