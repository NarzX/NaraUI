import os, sys, re, json, shutil, subprocess, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nara

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
GOLD = os.path.join(HERE, 'golden')
UPDATE = os.environ.get('NARA_UPDATE_GOLDEN') == '1'
HAS_V16 = hasattr(nara, 'compile_source')

def compile_code(code, live=False):
    if HAS_V16:
        res = nara.compile_source(code, 'test.nui', live)
        return res[0] if isinstance(res, tuple) else res
    lexer = nara.Lexer(code, 'test.nui')
    parser = nara.Parser(lexer.tokenize(), code, 'test.nui')
    res = nara.generate_code(parser.parse(), parser, live)
    return res[0] if isinstance(res, tuple) else res

def compile_file(name, live=False):
    with open(os.path.join(FIX, name)) as f:
        return compile_code(f.read(), live)

def expect_error(code):
    try:
        compile_code(code)
        return False
    except SystemExit:
        return True
    except BaseException as e:
        err = getattr(nara, 'NaraCompileError', None)
        if err and isinstance(e, err): return True
        raise

class TestLexer(unittest.TestCase):
    def test_comments_skipped(self):
        toks = nara.Lexer('// halo\n/* blok */\nApp "x" {}', 't.nui').tokenize()
        kinds = [t[0] for t in toks]
        self.assertNotIn('LINE_COMMENT', kinds)
        self.assertNotIn('BLOCK_COMMENT', kinds)
        self.assertIn('KEYWORD', kinds)

class TestParser(unittest.TestCase):
    def test_components_registered(self):
        code = 'Component Card2(a) { Text "{a}" {} }\nApp "x" { Card2("hi") {} }'
        lexer = nara.Lexer(code, 't.nui')
        parser = nara.Parser(lexer.tokenize(), code, 't.nui')
        parser.parse()
        self.assertIn('Card2', parser.components)

class TestGeneratorBasic(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.html = compile_file('basic.nui')
    def test_title(self): self.assertIn('<title>Basic Fixture</title>', self.html)
    def test_persist(self):
        self.assertIn("'count'", self.html)
        self.assertIn("localStorage.getItem('nara_'", self.html)
    def test_for_loop(self): self.assertIn('data-for="item in products"', self.html)
    def test_route(self): self.assertIn('data-route="/cart"', self.html)
    def test_dark_pipe(self): self.assertIn('body.dark-theme .n', self.html)
    def test_bind_src(self): self.assertIn('data-bind-src', self.html)
    def test_live_flag(self): self.assertIn('/nara-live-reload', compile_file('basic.nui', live=True))

class TestRegressionCanaries(unittest.TestCase):
    """Penjaga bug masa lalu: kalau salah satu ini lolos lagi, CI merah."""
    @classmethod
    def setUpClass(cls): cls.html = compile_file('basic.nui')
    def test_loopfix_present(self):
        self.assertIn('if (changed) callback();', self.html,
                      'REGRESI: change-detection deepProxy hilang -> infinite render loop!')
    def test_for_diffing_present(self):
        self.assertIn('__naraSig', self.html,
                      'REGRESI: For diffing hilang -> animasi restart tiap render!')
    def test_hashchange_correct(self):
        self.assertIn("new Event('hashchange')", self.html,
                      'REGRESI: event routing awal rusak!')
    def test_no_corruption_artifacts(self):
        for bad in ['hash change', '= >', '& &', 'document.bo dy', 'funct ion', '${e.target.value} "']:
            self.assertNotIn(bad, self.html, 'Korupsi/bug lama muncul lagi: ' + repr(bad))

class TestEngineJSSyntax(unittest.TestCase):
    def test_script_blocks_valid_js(self):
        node = shutil.which('node')
        if not node: self.skipTest('node tidak terpasang (opsional)')
        blocks = re.findall(r'<script>(.*?)</script>', compile_file('basic.nui'), re.S)
        self.assertTrue(blocks, 'Tidak ada script di output!')
        for i, js in enumerate(blocks):
            with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
                f.write(js); p = f.name
            r = subprocess.run([node, '--check', p], capture_output=True, text=True)
            os.unlink(p)
            self.assertEqual(r.returncode, 0, f'Script block #{i} SyntaxError:\n{r.stderr[:800]}')

@unittest.skipUnless(HAS_V16, 'butuh compiler v16+')
class TestV16Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.html = compile_file('features.nui')
    def test_else_chain(self):
        self.assertIn('nara-ifchain', self.html)
        self.assertEqual(self.html.count('class="nara-ifbranch"'), 3)
    def test_responsive_pipe(self): self.assertIn('@media (min-width: 768px)', self.html)
    def test_resource(self): self.assertIn('users_loading', self.html)
    def test_form_controls(self):
        for tag in ['<select', 'nara-toggle-ui', 'type="range"', '<textarea']:
            self.assertIn(tag, self.html)
    def test_emit_substituted(self):
        self.assertIn('MARKER_PARENT_OK', self.html)
        self.assertNotIn('emit(', self.html, 'emit() tidak tersubstitusi!')
    def test_precompiled_no_eval_spam(self):
        self.assertIn('NARA_EXPR_KEYS', self.html)
        self.assertEqual(self.html.count('new Function'), 1,
                         'new Function harus tinggal 1x (fallback saja) - CSP regression!')
    def test_route_transition(self): self.assertIn('t-slide', self.html)
    def test_devtools(self): self.assertIn('nara-debug', self.html)

@unittest.skipUnless(HAS_V16, 'butuh compiler v16+')
class TestBuild(unittest.TestCase):
    def test_build_pwa_embed(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(os.path.join(FIX, 'basic.nui'), os.path.join(td, 'basic.nui'))
            os.chdir(td)
            try:
                nara.do_build('basic.nui', pwa=True, embed=True)
                for fn in ['basic.html', 'manifest.webmanifest', 'sw.js', 'icon.svg', 'embed.js']:
                    self.assertTrue(os.path.exists(fn), fn + ' tidak dibuat')
                with open('basic.html', encoding='utf-8') as f: html = f.read()
                self.assertIn('manifest.webmanifest', html)
                self.assertIn('serviceWorker', html)
                with open('embed.js', encoding='utf-8') as f: self.assertIn('nara-app', f.read())
            finally:
                os.chdir(cwd)

class TestErrors(unittest.TestCase):
    def test_unclosed_block_raises(self):
        self.assertTrue(expect_error('App "x" { Text "a" { } '))
    def test_valid_app_ok(self):
        self.assertFalse(expect_error('App "x" { Container { Text "hi" {} } }'))

class TestGolden(unittest.TestCase):
    """Snapshot output; update sengaja via: python tests/test_compiler.py --update"""
    def test_golden_fixtures(self):
        os.makedirs(GOLD, exist_ok=True)
        for name in sorted(os.listdir(FIX)):
            if not name.endswith('.nui'): continue
            with self.subTest(fixture=name):
                html = compile_file(name)
                gpath = os.path.join(GOLD, name + '.html')
                if UPDATE:
                    with open(gpath, 'w') as f: f.write(html)
                    continue
                if not os.path.exists(gpath):
                    self.skipTest('golden belum ada; jalankan --update sekali')
                with open(gpath) as f: self.assertEqual(f.read(), html)

if __name__ == '__main__':
    if '--update' in sys.argv:
        os.environ['NARA_UPDATE_GOLDEN'] = '1'
        sys.argv.remove('--update')
    unittest.main(verbosity=2)

class TestLint(unittest.TestCase):
    def msgs(self, code): return [m for _, m in nara.lint_code(code)]
    def test_typo_prop(self):
        self.assertTrue(any('colr' in m and 'color' in m for m in self.msgs('App "x" { Text "a" { colr: red; } }')))
    def test_typo_tag(self):
        self.assertTrue(any('Buton' in m and 'Button' in m for m in self.msgs('App "x" { Buton "a" {} }')))
    def test_bad_animate(self):
        self.assertTrue(any('animate' in m for m in self.msgs('App "x" { Text "a" { animate: fade-in-upp; } }')))
    def test_bad_breakpoint(self):
        self.assertTrue(any('breakpoint' in m for m in self.msgs('App "x" { Text "a" { size: 14px | xd: 20px; } }')))
    def test_bind_unknown(self):
        self.assertTrue(any('bind' in m for m in self.msgs('App "x" { Input "a" { bind: belum; } }')))
    def test_unknown_ident(self):
        self.assertTrue(any('totalx' in m for m in self.msgs('App "x" { Text "{totalx}" {} }')))
    def test_fixtures_clean(self):
        for fx in ['basic.nui', 'features.nui']:
            with self.subTest(fixture=fx):
                hard = [m for _, m in nara.lint_code(open(os.path.join(FIX, fx), encoding='utf-8').read(), fx) if not m.startswith('info:')]
                self.assertEqual([], hard)
