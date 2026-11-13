"""Static consistency checks for Floe appearance (not a native QML test)."""
import json
import importlib.util
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
TOKENS = json.loads((ROOT / "design/tokens/floe.tokens.json").read_text())
THEME = (ROOT / "session/FloeTheme.qml").read_text()
SHELL = (ROOT / "session/shell.qml").read_text()
NIX = (ROOT / "modules/flow.nix").read_text()

def luminance(h):
    c = [int(h[i:i+2],16)/255 for i in (1,3,5)]
    v = [x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4 for x in c]
    return sum(a*b for a,b in zip(v,(.2126,.7152,.0722)))
def contrast(a,b):
    x,y = sorted((luminance(a),luminance(b)),reverse=True)
    return (x+.05)/(y+.05)

class FloeThemeTest(unittest.TestCase):
    def test_text_contrast(self):
        for mode in ("dark","light"):
            p = TOKENS["semantic"][mode]
            for surface in ("canvas","surface","surface-raised"):
                for fg in ("text-primary","text-secondary","text-muted"):
                    with self.subTest(mode=mode,surface=surface,text=fg):
                        self.assertGreaterEqual(contrast(p[surface]["$value"],p[fg]["$value"]),4.5)
    def test_qml_palette_matches_tokens(self):
        for mode in ("dark","light"):
            for role,entry in TOKENS["semantic"][mode].items():
                self.assertIn(entry["$value"],THEME,mode + "/" + role)

    def test_generated_files_are_in_sync_with_tokens(self):
        script = ROOT / "design/scripts/generate_theme.py"
        spec = importlib.util.spec_from_file_location("floe_generator",script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.render_qml(TOKENS),THEME)
        self.assertEqual(module.render_css(TOKENS),(ROOT / "design/generated/floe.css").read_text())

    def test_shell_integrates_theme(self):
        self.assertIn("FloeTheme { id: floeTheme;",SHELL)
        self.assertIn('"/floe-appearance.ini"',SHELL)
        self.assertIn("appearancePreferences.sync()",SHELL)
        self.assertIn("FloeTheme.qml",NIX)
        self.assertNotRegex(SHELL,r"#[0-9a-fA-F]{6}")
        self.assertIn("floeTheme.canvas",SHELL)
        self.assertIn("floeTheme.textPrimary",SHELL)

if __name__ == "__main__":
    unittest.main()
