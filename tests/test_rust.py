"""The Rust side of the code map: the scanner, the index it produces and what the site does with it.

Everything here reads `examples/rust-musing/prototype/lifelike/`, which exists to be read - the
crate carries a loop label, a trait impl, an associated const, enum variants and an inline test
module because each of those is a way the scanner can be wrong (adr-037).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.config import Config  # noqa: E402
from yss.evidence import Claim, _check_export  # noqa: E402
from yss.langs import label_for, spellings  # noqa: E402
from yss.langs.rust import file_index as rust_index  # noqa: E402
from yss.providers import symbols as symbols_provider  # noqa: E402
from yss.symbols import SUPPORTED_SUFFIXES, index_for, lookup, supported  # noqa: E402

CRATE = REPO / "examples/rust-musing/prototype/lifelike"


def scan(source: str) -> dict:
    """Index a snippet through the real entry point, so nothing under test is bypassed."""
    tmp = Path(tempfile.mkdtemp(prefix="yss-rust-"))
    try:
        f = tmp / "snippet.rs"
        f.write_text(source, encoding="utf-8")
        return rust_index(f, "snippet.rs")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class RustScannerTests(unittest.TestCase):
    """Constructs that a brace walker gets wrong if it is not careful."""

    def test_free_items_and_their_doc_comments(self):
        index = index_for(CRATE, "src/rules.rs")
        start, end, file = index["Rule"]
        self.assertEqual(file, "src/rules.rs")
        lines = (CRATE / "src/rules.rs").read_text(encoding="utf-8").splitlines()
        # An item starts at its documentation, the way a Python one starts at its decorator.
        self.assertTrue(lines[start - 1].startswith("///"))
        self.assertIn("pub struct Rule", "\n".join(lines[start - 1:end]))

    def test_inherent_and_trait_impls_index_under_the_type(self):
        index = index_for(CRATE, "src/board.rs")
        self.assertIn("Board::step", index)
        # `impl fmt::Display for Board` - the member belongs to the type, not to the trait.
        self.assertIn("Board::fmt", index)
        self.assertNotIn("Display::fmt", index)

    def test_associated_const_and_enum_variants(self):
        index = index_for(CRATE, "src/rules.rs")
        self.assertIn("Rule::LIFE", index)
        self.assertIn("ParseError::CountOutOfRange", index)
        self.assertIn("ParseError::MissingSlash", index)

    def test_inline_module_members(self):
        index = index_for(CRATE, "src/rules.rs")
        self.assertIn("tests", index)
        self.assertIn("tests::seeds_never_survives", index)

    def test_use_renames_are_indexed_and_globs_are_not(self):
        index = index_for(CRATE, "src/board.rs")
        self.assertIn("LifeRule", index)     # use crate::rules::Rule as LifeRule;
        self.assertNotIn("*", index)

    def test_a_loop_label_is_not_a_character_literal(self):
        """`'parse:` never closes; reading it as a char would swallow the rest of the file."""
        index = index_for(CRATE, "src/main.rs")
        self.assertIn("Options::from_args", index)
        self.assertIn("main", index)

    def test_nothing_inside_a_function_body_is_an_export(self):
        index = scan("fn outer() {\n    fn helper() {}\n    const K: u8 = 1;\n}\n")
        self.assertIn("outer", index)
        self.assertNotIn("helper", index)
        self.assertNotIn("K", index)

    def test_braces_in_strings_and_comments_do_not_close_a_block(self):
        source = (
            'fn a() {\n    let s = "} fn ghost() {";\n    // } fn ghost2() {\n'
            '    let r = r#"} fn ghost3() {"#;\n}\n\nfn b() {}\n'
        )
        index = scan(source)
        self.assertEqual(sorted(index), ["a", "b"])

    def test_nested_block_comments(self):
        index = scan("/* outer /* inner */ still a comment */\nfn only() {}\n")
        self.assertEqual(sorted(index), ["only"])

    def test_attributes_extend_the_start_line_backwards(self):
        source = "/// doc\n#[derive(Debug)]\n#[repr(C)]\npub struct S {\n    x: u8,\n}\n"
        index = scan(source)
        self.assertEqual(index["S"], (1, 6))

    def test_modifiers_before_a_keyword(self):
        source = (
            'pub(crate) const fn a() -> u8 { 0 }\n'
            'pub async unsafe fn b() {}\n'
            'pub extern "C" fn c() {}\n'
            'pub static mut D: u8 = 0;\n'
            'pub unsafe trait E {}\n'
        )
        self.assertEqual(sorted(scan(source)), ["D", "E", "a", "b", "c"])

    def test_struct_fields_are_not_indexed(self):
        """`Type::field` is not how Rust spells one, and it would shadow a method of that name."""
        index = scan("pub struct S {\n    pub step: u8,\n}\nimpl S {\n    pub fn step(&self) {}\n}\n")
        self.assertIn("S::step", index)
        lines = index["S::step"]
        self.assertEqual(lines[0], 5)   # the method, not the field on line 2

    def test_an_unreadable_file_yields_nothing_rather_than_raising(self):
        self.assertEqual(scan(""), {})


class RustLookupTests(unittest.TestCase):
    def test_rust_is_a_supported_suffix(self):
        self.assertIn(".rs", SUPPORTED_SUFFIXES)
        self.assertTrue(supported("src/board.rs"))
        self.assertEqual(label_for(".rs"), "rust")
        self.assertEqual(label_for(".py"), "python")
        self.assertEqual(label_for(".md"), "")

    def test_a_member_resolves_under_either_separator(self):
        """A code map written in the Python house style still finds the lines it names."""
        self.assertEqual(lookup(CRATE, "src/rules.rs", "Rule::apply"),
                         lookup(CRATE, "src/rules.rs", "Rule.apply"))
        self.assertIsNotNone(lookup(CRATE, "src/rules.rs", "Rule.apply"))
        self.assertIn("Rule::apply", spellings(".rs", "Rule.apply"))

    def test_a_directory_prefixes_by_stem_and_keeps_the_crate_root_bare(self):
        index = index_for(CRATE, "src/")
        self.assertIn("board::Board", index)
        self.assertIn("rules::Rule::apply", index)
        self.assertEqual(index["board::Board"][2], "src/board.rs")
        self.assertIn("main", index)                 # main.rs is a module root, so its names are bare
        self.assertNotIn("main::main", index)

    def test_a_missing_name_is_none_not_an_exception(self):
        self.assertIsNone(lookup(CRATE, "src/board.rs", "no_such_thing"))


class RustCodeMapTests(unittest.TestCase):
    """The fixture collection, end to end: every claim it makes about the crate holds."""

    def setUp(self):
        self.cfg = Config.load(REPO)

    def test_every_rust_export_the_collection_cites_resolves(self):
        data = symbols_provider.collect(self.cfg, {"_target": "public"})
        rust = [u for u in data["unresolved"] if u["path"].endswith((".rs", "/src/"))]
        self.assertEqual(rust, [], "a cited Rust export is not where the code map says")
        self.assertTrue(any(p.endswith(".rs") for p in data["modules"]))

    def test_the_index_carries_a_language_and_definitions_per_file(self):
        data = symbols_provider.collect(self.cfg, {"_target": "public"})
        board = "examples/rust-musing/prototype/lifelike/src/board.rs"
        self.assertEqual(data["langs"][board], "rust")
        # `defs` is spelled the way the file spells it, because that is what a reader clicks on.
        self.assertIn("Board::step", data["defs"][board])
        self.assertNotIn("board::Board", data["defs"][board])

    def test_defs_can_be_switched_off(self):
        data = symbols_provider.collect(self.cfg, {"_target": "public", "defs": False})
        self.assertNotIn("defs", data)

    def test_whole_files_are_private_only_and_capped(self):
        data = symbols_provider.collect_source(self.cfg, {"_target": "private"})
        board = "examples/rust-musing/prototype/lifelike/src/board.rs"
        self.assertIn(board, data["files"])
        self.assertGreater(len(data["files"][board]["lines"]), 100)
        self.assertEqual(data["files"][board]["lang"], "rust")
        with self.assertRaises(RuntimeError):
            symbols_provider.collect_source(self.cfg, {"_target": "public"})

    def test_a_tiny_line_budget_skips_files_rather_than_truncating_them(self):
        data = symbols_provider.collect_source(self.cfg, {"_target": "private", "max_file_lines": 5})
        self.assertEqual(data["files"], {})
        self.assertTrue(data["files_skipped"])

    def test_no_absolute_path_reaches_the_index(self):
        data = symbols_provider.collect(self.cfg, {"_target": "public"})
        self.assertNotIn(str(REPO), json.dumps(data))


class CollectionExportEvidenceTests(unittest.TestCase):
    """A code map inside a collection cites paths relative to that collection, exports included."""

    def setUp(self):
        self.cfg = Config.load(REPO)
        self.doc = {"_collection": "rust-musing"}

    def _claim(self, target: str) -> Claim:
        claim = Claim("rust-musing/codemap", None, "modules/0/exports/0/name", "export", target)
        _check_export(self.cfg, self.doc, claim)
        return claim

    def test_a_collection_relative_export_resolves(self):
        claim = self._claim("prototype/lifelike/src/rules.rs::Rule::apply")
        self.assertEqual(claim.status, "ok", claim.detail)
        self.assertIn("src/rules.rs:", claim.detail)

    def test_a_renamed_export_is_stale(self):
        claim = self._claim("prototype/lifelike/src/rules.rs::Rule::applied")
        self.assertEqual(claim.status, "stale")

    def test_a_member_name_survives_the_split(self):
        """`<path>::<name>` split from the right would read `Rule` as part of the path."""
        claim = self._claim("prototype/lifelike/src/board.rs::Board::step")
        self.assertEqual(claim.status, "ok", claim.detail)


class EditorBridgeTests(unittest.TestCase):
    """The bridge is an upgrade, so what is asserted is that it is optional and repo-relative."""

    def setUp(self):
        self.prefab = (REPO / "yss/prefabs/module-list.yaml").read_text(encoding="utf-8")
        self.extension = (REPO / "tools/vscode-yss/extension.js").read_text(encoding="utf-8")

    def test_the_page_announces_itself_and_waits_to_be_answered(self):
        self.assertIn('"yss-code"', self.prefab)
        self.assertIn('type: "hello"', self.prefab)
        # The buttons exist only once a host has said `ready`; nothing is assumed from being framed.
        self.assertIn("editor = { name:", self.prefab)

    def test_the_extension_refuses_a_path_that_leaves_the_workspace(self):
        self.assertIn('p === ".."', self.extension)
        self.assertIn("path.isAbsolute(inside)", self.extension)

    def test_no_absolute_path_is_sent_from_the_page(self):
        """The host knows where the repository is; the page only ever names a file inside it."""
        for message in ("open", "reveal", "definition"):
            self.assertIn(f'toEditor("{message}"', self.prefab)
        self.assertIn("file: wrap._file", self.prefab)


if __name__ == "__main__":
    unittest.main()
