import os
import tempfile
import textwrap
import unittest

import customtkinter as ctk

import nurestore as nm


class VersionGtTests(unittest.TestCase):
    def test_basic_ordering(self):
        self.assertTrue(nm._version_gt("1.0.1", "1.0.0"))
        self.assertTrue(nm._version_gt("2.0.0", "1.9.9"))
        self.assertFalse(nm._version_gt("1.0.0", "1.0.0"))
        self.assertFalse(nm._version_gt("1.0.0", "1.0.1"))

    def test_build_metadata_ignored(self):
        # Build metadata (after "+") shouldn't change the comparison result.
        self.assertFalse(nm._version_gt("1.0.0+build", "1.0.0"))
        self.assertFalse(nm._version_gt("1.0.0", "1.0.0+build"))


class NuGetSourceTests(unittest.TestCase):
    def test_copy_is_independent(self):
        s = nm.NuGetSource("k", "https://x/v3", True, "u", "p", "User", True)
        c = s.copy()
        c.url = "https://y"
        self.assertEqual(s.url, "https://x/v3")
        self.assertEqual(c.username, "u")


class ParseConfigTests(unittest.TestCase):
    def test_parses_sources_and_disabled_and_creds(self):
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <configuration>
              <packageSources>
                <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
                <add key="my.feed"   value="https://feed.example/index.json" />
              </packageSources>
              <disabledPackageSources>
                <add key="my.feed" value="true" />
              </disabledPackageSources>
              <packageSourceCredentials>
                <my_x002E_feed>
                  <add key="Username" value="alice" />
                  <add key="ClearTextPassword" value="secret" />
                </my_x002E_feed>
              </packageSourceCredentials>
            </configuration>
            """)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "NuGet.Config")
            with open(path, "w", encoding="utf-8") as f:
                f.write(xml)
            sources, clear_all = nm._parse_config(path, "User", True)

        self.assertFalse(clear_all)
        keys = {s.key: s for s in sources}
        self.assertIn("nuget.org", keys)
        self.assertIn("my.feed", keys)
        self.assertTrue(keys["nuget.org"].enabled)
        self.assertFalse(keys["my.feed"].enabled)
        self.assertEqual(keys["my.feed"].username, "alice")
        self.assertEqual(keys["my.feed"].password, "secret")

    def test_machine_config_paths_returns_list(self):
        # Smoke: function should return a list, never raise.
        result = nm._machine_config_paths()
        self.assertIsInstance(result, list)

    def test_load_all_sources_includes_explicit_config_file(self):
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <configuration>
              <packageSources>
                <add key="extra.feed" value="https://feed.example/index.json" />
              </packageSources>
            </configuration>
            """)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "custom.config")
            with open(cfg, "w", encoding="utf-8") as f:
                f.write(xml)

            sources = nm._load_all_sources(tmp, [cfg])

        loaded = next((s for s in sources if s.key == "extra.feed"), None)
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.origin.startswith("Loaded Config"))
        self.assertFalse(loaded.user_managed)


class SettingsTests(unittest.TestCase):
    def test_save_and_load_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            original = nm._settings_path
            try:
                nm._settings_path = lambda: path
                data = {
                    "last_base_dir": r"D:\Repos\ams-auth-service",
                    "loaded_config_path": r"D:\Repos\ams-auth-service\NuGet.Config",
                }
                nm._save_settings(data)
                self.assertEqual(nm._load_settings(), data)
            finally:
                nm._settings_path = original


class ProjectBadgeTests(unittest.TestCase):
    def test_project_scope_badge_for_solution(self):
        self.assertEqual(
            nm._project_scope_badge(2, 5, True),
            "· 2 of 5 projects")

    def test_project_scope_badge_hidden_for_single_project(self):
        self.assertEqual(nm._project_scope_badge(1, 1, False), "")


class PackageListLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.lst = nm.PackageList(self.root, on_select=lambda _: None)
        self.lst.pack()

    def tearDown(self):
        self.lst.destroy()

    def test_compact_row_has_no_author_or_meta(self):
        self.lst.add_item("Foo.Bar", version="1.2.3", version_inline=True)
        meta = self.lst._items["Foo.Bar"]
        self.assertTrue(meta["compact"])
        self.assertFalse(meta["has_author"])
        self.assertFalse(meta["has_meta"])

    def test_browse_row_has_author_and_meta(self):
        self.lst.add_item("Foo.Bar", author="Alice", version="1.2.3",
                          downloads="1,234")
        meta = self.lst._items["Foo.Bar"]
        self.assertFalse(meta["compact"])
        self.assertTrue(meta["has_author"])
        self.assertTrue(meta["has_meta"])

    def test_compact_row_uses_fewer_widgets_than_full_row(self):
        # Compact mode should skip the author label AND the meta_row frame,
        # so the row's TK child count is strictly smaller than a full row.
        self.lst.add_item("Compact.Pkg", version="1.0.0", version_inline=True)
        self.lst.add_item("Full.Pkg",    version="1.0.0",
                          author="Alice", downloads="42")
        self.root.update_idletasks()

        compact_row = self.lst._items["Compact.Pkg"]["row"]
        full_row    = self.lst._items["Full.Pkg"]["row"]

        compact_kids = len(compact_row.winfo_children())
        full_kids    = len(full_row.winfo_children())
        self.assertLess(compact_kids, full_kids,
                        f"compact row has {compact_kids} children, "
                        f"full row has {full_kids}")

    def test_compact_row_uses_only_one_grid_row(self):
        # All compact-row widgets must live in grid row 0.
        self.lst.add_item("Compact.Pkg", version="1.0.0", version_inline=True)
        row = self.lst._items["Compact.Pkg"]["row"]
        rows_used = {
            child.grid_info().get("row")
            for child in row.winfo_children()
            if child.grid_info()
        }
        self.assertEqual(rows_used, {0},
                         f"compact row should only use grid row 0, "
                         f"got {rows_used}")

    def test_compact_row_requested_height_stays_small(self):
        self.lst.add_item("Compact.Pkg", version="1.0.0", version_inline=True)
        self.root.update_idletasks()

        row = self.lst._items["Compact.Pkg"]["row"]
        req_height = row.winfo_reqheight()

        self.assertLessEqual(
            req_height, 40,
            f"compact row requested height should stay compact, got {req_height}")

    def test_message_shows_placeholder(self):
        self.lst.message("nothing here")
        self.root.update_idletasks()
        labels = [w for w in self.lst.winfo_children()
                  if isinstance(w, ctk.CTkLabel)]
        # message() clears items, so _items must be empty.
        self.assertEqual(self.lst._items, {})
        self.assertTrue(any("nothing" in lbl.cget("text") for lbl in labels))


if __name__ == "__main__":
    unittest.main()
