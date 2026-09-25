import sys, os, re, time, threading, json, traceback
from http.server import SimpleHTTPRequestHandler, HTTPServer, BaseHTTPRequestHandler

NARA_VERSION = "NaraUI v16.0.0-pro"
BP = {'sm': 640, 'md': 768, 'lg': 1024, 'xl': 1280}
SKIP_PROPS = ['on-click', 'bind', 'hover-scale', 'hover-shadow', 'hover-bg', '_args', 'on-swipe-left',
              'on-swipe-right', 'on-context-menu', 'sound', 'draggable', 'min', 'max', 'step',
              'transition', 'value', '_else']

class NaraCompileError(Exception): pass

def esc_attr(s): return s.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')

# ==========================================
# 1. LEXER
# ==========================================
class Lexer:
    def __init__(self, code, filename="app.nui"):
        self.code = code; self.filename = filename

    def tokenize(self):
        tokens = []
        token_specification = [
            ('BLOCK_COMMENT', r'/\*.*?\*/'),
            ('LINE_COMMENT',  r'//[^\n]*'),
            ('STRING',        r'"[^"]*"'),
            ('LBRACE',        r'\{'), ('RBRACE', r'\}'),
            ('LPAREN',        r'\('), ('RPAREN', r'\)'),
            ('COMMA',         r','),  ('SEMI',   r';'),
            ('PERSIST',       r'@persist'),
            ('KEYWORD',       r'\b(App|state|computed|on-mount|import|Route|Component|Slot|For|If|Else|Modal|Grid|resource)\b'),
            ('ID',            r'[a-zA-Z_][a-zA-Z0-9_-]*'),
            ('CSS_VAL',       r'#[0-9a-fA-F]+|[0-9]+px|[0-9]+%|rgba\([^)]+\)|linear-gradient\([^)]+\)'),
            ('WS',            r'\s+'),
            ('OTHER',         r'.'),
        ]
        tok_regex = '|'.join('(?P<%s>%s)' % pair for pair in token_specification)
        line, col = 1, 1
        for mo in re.finditer(tok_regex, self.code, re.DOTALL):
            kind, value = mo.lastgroup, mo.group()
            if kind in ('WS', 'LINE_COMMENT', 'BLOCK_COMMENT'):
                if '\n' in value: line += value.count('\n'); col = 1
                else: col += len(value)
                continue
            tokens.append((kind, value, line, col, mo.start(), mo.end()))
            col += len(value)
        return tokens

# ==========================================
# 2. PARSER & AST
# ==========================================
class ASTNode:
    def __init__(self, type, name="", param="", props=None, children=None, line=0):
        self.type = type; self.name = name; self.param = param
        self.props = props or {}; self.children = children or []
        self.line = line; self.props_lines = {}

class Parser:
    def __init__(self, tokens, raw_code, filename):
        self.tokens, self.raw_code, self.filename = tokens, raw_code, filename
        self.pos = 0; self.components = {}; self.imported = set()

    def error(self, msg, line, col):
        lines = self.raw_code.split('\n')
        src_line = lines[line-1] if 0 < line <= len(lines) else ''
        frame = f"{self.filename}:{line}:{col}\n    {src_line.strip()}\n    {' '*(col-1)}^"
        raise NaraCompileError(f"[NARAUI ERROR] {frame}\n    -> {msg}")

    def current(self): return self.tokens[self.pos] if self.pos < len(self.tokens) else ('EOF','',0,0,0,0)

    def consume(self, expected_kind):
        curr = self.current()
        if curr[0] == expected_kind: self.pos += 1; return curr[1]
        self.error(f"Expected {expected_kind}, got '{curr[1]}'", curr[2], curr[3])

    def parse(self):
        root = ASTNode('Root')
        while self.current()[0] != 'EOF':
            curr = self.current()
            if curr[0] == 'KEYWORD' and curr[1] == 'Component': self.parse_component_def()
            elif curr[0] == 'KEYWORD' and curr[1] == 'import': self.parse_import()
            elif curr[0] == 'KEYWORD' and curr[1] == 'App': root.children.append(self.parse_app())
            else:
                parsed_global = self.parse_logic_block()
                if parsed_global: root.children.append(parsed_global)
                else: self.pos += 1
        return root

    def parse_import(self):
        self.consume('KEYWORD')
        self.consume('ID')
        if self.current()[0] == 'ID' and self.current()[1] == 'from': self.pos += 1
        path = self.consume('STRING').strip('"')
        if self.current()[0] == 'SEMI': self.pos += 1
        base = os.path.dirname(os.path.abspath(self.filename))
        ap = os.path.abspath(os.path.join(base, path))
        if ap in self.imported: return
        self.imported.add(ap)
        if not os.path.exists(ap): self.error(f"File import tidak ditemukan: {path}", self.current()[2], self.current()[3])
        with open(ap) as f: code = f.read()
        sub = Parser(Lexer(code, ap).tokenize(), code, ap)
        sub.imported = self.imported
        sub.parse()
        self.components.update(sub.components)

    def parse_logic_block(self, is_persist=False):
        curr = self.current()
        if curr[0] == 'PERSIST':
            self.pos += 1
            return self.parse_logic_block(is_persist=True)
        if curr[0] == 'KEYWORD' and curr[1] == 'state':
            self.pos += 1
            if self.current()[0] == 'OTHER' and self.current()[1] == ':': self.pos += 1
            start_idx = self.current()[4]
            while self.current()[0] not in ('SEMI', 'EOF'): self.pos += 1
            end_idx = self.current()[4]
            val = self.raw_code[start_idx:end_idx].strip()
            if val.endswith(';'): val = val[:-1]
            val = val.replace('=', ':', 1)
            self.consume('SEMI')
            return ASTNode('PersistState' if is_persist else 'State', param=val)
        if curr[0] == 'KEYWORD' and curr[1] == 'computed':
            self.pos += 1
            if self.current()[0] == 'OTHER' and self.current()[1] == ':': self.pos += 1
            start_idx = self.current()[4]
            while self.current()[0] not in ('SEMI', 'EOF'): self.pos += 1
            end_idx = self.current()[4]
            val = self.raw_code[start_idx:end_idx].strip()
            self.consume('SEMI')
            return ASTNode('Computed', param=val)
        if curr[0] == 'KEYWORD' and curr[1] == 'resource':
            self.pos += 1
            if self.current()[0] == 'OTHER' and self.current()[1] == ':': self.pos += 1
            start_idx = self.current()[4]
            while self.current()[0] not in ('SEMI', 'EOF'): self.pos += 1
            end_idx = self.current()[4]
            val = self.raw_code[start_idx:end_idx].strip()
            self.consume('SEMI')
            return ASTNode('Resource', param=val)
        if curr[0] == 'KEYWORD' and curr[1] == 'on-mount':
            self.pos += 1
            if self.current()[0] == 'OTHER' and self.current()[1] == ':': self.pos += 1
            if self.current()[0] == 'LBRACE':
                self.pos += 1
                start_idx = self.current()[4]
                brace_lvl = 1
                while brace_lvl > 0 and self.current()[0] != 'EOF':
                    if self.current()[0] == 'LBRACE': brace_lvl += 1
                    elif self.current()[0] == 'RBRACE': brace_lvl -= 1
                    if brace_lvl == 0: break
                    self.pos += 1
                end_idx = self.current()[4]
                self.consume('RBRACE')
                return ASTNode('OnMount', param=self.raw_code[start_idx:end_idx].strip())
        return None

    def parse_component_def(self):
        self.consume('KEYWORD')
        name = self.consume('ID')
        params = ""
        if self.current()[0] == 'LPAREN':
            self.consume('LPAREN')
            start_idx = self.current()[4]
            while self.current()[0] != 'RPAREN': self.pos += 1
            params = self.raw_code[start_idx:self.current()[4]]
            self.consume('RPAREN')
        self.components[name] = self.parse_block(type='ComponentDef', name=name, param=params)

    def parse_app(self):
        self.consume('KEYWORD')
        return self.parse_block(type='App', name=self.consume('STRING').strip('"'))

    def parse_block(self, type, name="", param=""):
        self.consume('LBRACE')
        node = ASTNode(type, name, param)
        while self.current()[0] not in ('RBRACE', 'EOF'):
            curr = self.current()
            parsed_logic = self.parse_logic_block()
            if parsed_logic:
                node.children.append(parsed_logic); continue
            if curr[0] in ('ID', 'KEYWORD') and self.pos+1 < len(self.tokens) and self.tokens[self.pos+1][0] == 'OTHER' and self.tokens[self.pos+1][1] == ':':
                prop_key = self.consume(curr[0]); prop_line = curr[2]; self.consume('OTHER')
                start_idx = self.current()[4]; brace_lvl = 0; has_brace = False
                while self.current()[0] != 'EOF':
                    curr_tok = self.current()[0]
                    if curr_tok == 'LBRACE': brace_lvl += 1; has_brace = True
                    elif curr_tok == 'RBRACE':
                        if brace_lvl == 0: end_idx = self.current()[4]; break
                        brace_lvl -= 1
                        if has_brace and brace_lvl == 0:
                            self.pos += 1; end_idx = self.current()[4]
                            if self.current()[0] == 'SEMI': self.pos += 1
                            break
                    elif curr_tok == 'SEMI' and brace_lvl == 0:
                        end_idx = self.current()[4]; self.pos += 1; break
                    self.pos += 1
                raw_prop = self.raw_code[start_idx:end_idx].strip()
                if raw_prop.endswith(';'): raw_prop = raw_prop[:-1].strip()
                node.props[prop_key] = raw_prop; node.props_lines[prop_key] = prop_line
            elif curr[0] in ('ID', 'KEYWORD'):
                child_tag = curr[1]; self.pos += 1
                child_param = ""
                if self.current()[0] == 'STRING': child_param = self.consume('STRING').strip('"')
                args = []
                if self.current()[0] == 'LPAREN':
                    self.consume('LPAREN')
                    start_idx = self.current()[4]
                    while self.current()[0] != 'RPAREN': self.pos += 1
                    args_raw = self.raw_code[start_idx:self.current()[4]]
                    args = [a.strip().strip('"') for a in args_raw.split(',')] if args_raw else []
                    self.consume('RPAREN')
                if child_tag in self.components:
                    comp_node = self.parse_block(type='ComponentInstance', name=child_tag, param=child_param)
                    comp_node.line = curr[2]
                    comp_node.props['_args'] = args
                    node.children.append(comp_node)
                elif self.current()[0] == 'LBRACE':
                    child_node = self.parse_block(type='Element', name=child_tag, param=child_param)
                    child_node.line = curr[2]
                    if child_tag == 'If':
                        branches = []
                        while self.current()[0] in ('ID', 'KEYWORD') and self.current()[1] == 'Else':
                            self.pos += 1
                            if self.current()[0] in ('ID', 'KEYWORD') and self.current()[1] == 'If':
                                self.pos += 1
                                cond = self.consume('STRING').strip('"')
                                blk = self.parse_block(type='Element', name='ElseIfBlock', param=cond)
                                branches.append((cond, blk))
                            elif self.current()[0] == 'LBRACE':
                                blk = self.parse_block(type='Element', name='ElseBlock')
                                branches.append((None, blk))
                            else:
                                self.error("Expected block atau If setelah Else", self.current()[2], self.current()[3])
                        child_node.props['_else'] = branches
                    node.children.append(child_node)
                else: self.error(f"Sintaks tidak valid: {child_tag}", curr[2], curr[3])
            else: self.pos += 1
        self.consume('RBRACE')
        return node

# ==========================================
# 3. CODE GENERATOR
# ==========================================
def generate_code(ast_root, parser, is_live=False):
    app_title = "NaraUI App"
    for child in ast_root.children:
        if child.type == 'App': app_title = child.name

    css_out = ""
    states, persist_keys, computed, on_mounts = [], [], [], []
    expr_keys, actions = {}, {}

    def reg_expr(src):
        if src not in expr_keys: expr_keys[src] = f"e{len(expr_keys)}"
        return expr_keys[src]
    def reg_action(src):
        k = f"a{len(actions)}"; actions[k] = src; return k

    global_css = f"/* {NARA_VERSION} */\n"
    global_css += "* { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }\n"
    global_css += "body { width: 100%; min-height: 100vh; overflow-x: hidden; background: #f8fafc; color: #0f172a; display: flex; flex-direction: column; align-items: center; justify-content: flex-start; transition: background 0.3s, color 0.3s; }\n"
    global_css += "@media (prefers-color-scheme: dark) { body:not(.light-theme) { background: #0f172a; color: white; } }\n"
    global_css += "body.dark-theme { background: #0f172a; color: white; }\n"
    global_css += ".nara-container { padding: 40px; border-radius: 20px; text-align: center; width: 100%; max-width: 1200px; margin-top: 2vh; transition: 0.3s; }\n"
    global_css += ".nara-card { border: 1px solid rgba(128,128,128,0.2); border-radius: 16px; padding: 20px; display: flex; flex-direction: column; transition: 0.3s; height: fit-content; }\n"
    global_css += ".nara-row { display: flex; flex-direction: row; gap: 15px; align-items: center; width: 100%; }\n"
    global_css += ".nara-column { display: flex; flex-direction: column; gap: 15px; align-items: center; width: 100%; }\n"
    global_css += ".nara-element { transition: all 0.3s; }\n"
    global_css += "button { cursor: pointer; border: none; font-weight: bold; padding: 12px 24px; border-radius: 8px; transition: 0.3s; }\n"
    global_css += ".nara-draggable { position: absolute; user-select: none; touch-action: none; will-change: transform, left, top; margin: 0 !important; }\n"
    global_css += ".nara-draggable:active { cursor: grabbing !important; }\n"
    global_css += ".nara-route { display: none; flex-direction: column; width: 100%; align-items: center; animation: fadeInUp 0.3s ease-out; }\n"
    global_css += ".nara-route.active { display: flex; }\n"
    global_css += ".nara-route.t-slide.active { animation: slideIn .35s ease; }\n"
    global_css += ".nara-route.t-zoom.active { animation: zoomIn .3s ease; }\n"
    global_css += ".nara-route.t-fade.active { animation: fadeIn .4s ease; }\n"
    global_css += ".nara-toast-style { position: fixed; bottom: -50px; left: 50%; transform: translateX(-50%); background: #1e293b; color: white; padding: 12px 24px; border-radius: 50px; z-index: 999999; opacity: 0; transition: all 0.4s; pointer-events: none; }\n"
    global_css += ".nara-toast-style.show { opacity: 1; bottom: 40px; }\n"
    global_css += ".nara-field { display: flex; align-items: center; gap: 10px; width: 100%; cursor: pointer; }\n"
    global_css += ".nara-field-label { color: inherit; }\n"
    global_css += ".nara-toggle-input { display: none; }\n"
    global_css += ".nara-toggle-ui { width: 46px; height: 26px; background: #475569; border-radius: 999px; position: relative; transition: .3s; flex-shrink: 0; }\n"
    global_css += ".nara-toggle-ui::after { content: ''; position: absolute; width: 20px; height: 20px; border-radius: 50%; background: white; top: 3px; left: 3px; transition: .3s; }\n"
    global_css += ".nara-toggle-input:checked + .nara-toggle-ui { background: #10b981; }\n"
    global_css += ".nara-toggle-input:checked + .nara-toggle-ui::after { left: 23px; }\n"
    global_css += ".nara-checkbox { width: 18px; height: 18px; accent-color: #3b82f6; }\n"
    global_css += ".nara-slider { width: 100%; accent-color: #3b82f6; }\n"
    global_css += ".nara-select, .nara-textarea { background: #1e293b; color: white; border: 1px solid #334155; border-radius: 10px; padding: 12px; font-family: inherit; font-size: 14px; width: 100%; }\n"
    global_css += ".nara-textarea { min-height: 120px; resize: vertical; }\n"
    global_css += "@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }\n"
    global_css += "@keyframes fadeInUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }\n"
    global_css += "@keyframes popOut { from { opacity: 0; transform: scale(0.8); } to { opacity: 1; transform: scale(1); } }\n"
    global_css += "@keyframes slideIn { from { opacity: 0; transform: translateX(50px); } to { opacity: 1; transform: none; } }\n"
    global_css += "@keyframes zoomIn { from { opacity: 0; transform: scale(.9); } to { opacity: 1; transform: none; } }\n"
    global_css += "@media (max-width: 768px) { .nara-container { padding: 15px; border-radius: 0; margin-top: 0; min-height: 100vh; overflow-x: hidden; } }\n"

    class_counter = 0

    def process_node(node, component_args=None, slot_html="", component_events=None):
        nonlocal class_counter, css_out
        if node.type in ('State', 'PersistState'):
            k = node.param.split(':')[0].strip()
            states.append(node.param)
            if node.type == 'PersistState': persist_keys.append(f"'{k}'")
            return ""
        if node.type == 'Computed': computed.append(node.param); return ""
        if node.type == 'OnMount': on_mounts.append(node.param); return ""
        if node.type == 'Resource':
            m = re.match(r'(\w+)\s*=\s*fetch\((.*)\)\s*$', node.param)
            if not m: return ""
            name, args = m.group(1), m.group(2).strip()
            states.append(f"{name}: []"); states.append(f"{name}_loading: true"); states.append(f"{name}_error: ''")
            on_mounts.append(f"fetch({args}).then(res => res.json()).then(data => {{ {name} = data; {name}_loading = false; }}).catch(err => {{ {name}_error = err.message; {name}_loading = false; }});")
            return ""
        if node.type == 'App': return f'<div id="app-root" style="width: 100%; display: flex; flex-direction: column; align-items: center; position: relative;">{"".join(process_node(c) for c in node.children)}</div>'
        if node.type == 'ComponentInstance':
            comp_def = parser.components.get(node.name)
            if not comp_def: return ""
            def_params = [p.strip() for p in comp_def.param.split(',')] if comp_def.param else []
            passed_args = node.props.get('_args', [])
            scope_map = {def_params[i]: passed_args[i] for i in range(min(len(def_params), len(passed_args)))}
            events = {pk[3:]: pv for pk, pv in node.props.items() if pk.startswith('on-')}
            return "".join(process_node(c, component_args=scope_map, slot_html="".join(process_node(ch) for ch in node.children), component_events=events) for c in comp_def.children)
        if node.type == 'Element' and node.name == 'Slot': return slot_html

        cname = f"n{class_counter}"; class_counter += 1
        styles = []; is_draggable = False
        for k, v in node.props.items():
            if k in SKIP_PROPS:
                if k == 'draggable' and v == 'true': is_draggable = True
                continue
            css_k = k.replace('size', 'font-size').replace('weight', 'font-weight').replace('radius', 'border-radius')
            if k == 'animate':
                anim_map = { 'fade-in': 'fadeIn 0.5s ease-out forwards', 'fade-in-up': 'fadeInUp 0.5s ease-out forwards', 'pop-out': 'popOut 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards' }
                styles.append(f"animation: {anim_map.get(v.strip(), v.strip())};")
                continue
            parts = [p.strip() for p in v.split('|')]
            styles.append(f"{css_k}: {parts[0]};")
            for p in parts[1:]:
                if p.startswith('dark:'):
                    dv = p[5:].strip()
                    css_out += f"body.dark-theme .{cname} {{ {css_k}: {dv} !important; }}\n"
                    css_out += f"@media (prefers-color-scheme: dark) {{ body:not(.light-theme) .{cname} {{ {css_k}: {dv} !important; }} }}\n"
                else:
                    mbp = re.match(r'^(sm|md|lg|xl):\s*(.+)$', p)
                    if mbp: css_out += f"@media (min-width: {BP[mbp.group(1)]}px) {{ .{cname} {{ {css_k}: {mbp.group(2).strip()} !important; }} }}\n"

        if 'hover-scale' in node.props or 'hover-shadow' in node.props or 'hover-bg' in node.props:
            h_scale, h_shadow, bg_rule = node.props.get('hover-scale', '1'), node.props.get('hover-shadow', 'none'), node.props.get('hover-bg')
            css_out += f".{cname}:hover {{ transform: scale({h_scale}) translateY(-2px); box-shadow: {h_shadow}; {'background: '+bg_rule if bg_rule else ''} }}\n"
        if styles: css_out += f".{cname} {{ {' '.join(styles)} }}\n"

        tag = node.name; param = node.param
        if component_args and '{' in param:
            for k, v in component_args.items(): param = param.replace(f"{{{k}}}", f"{{{v}}}")

        def process_action(action):
            if action.startswith('{') and action.endswith('}'): action = action[1:-1].strip()
            if component_args:
                for k, v in component_args.items(): action = re.sub(rf'\b{k}\b', v, action)
            if component_events:
                action = re.sub(r'emit\(\s*["\']([^"\']+)["\']\s*\)', lambda mm: '(' + component_events.get(mm.group(1), '') + ')', action)
            else:
                action = re.sub(r'emit\(\s*["\']([^"\']+)["\']\s*\)', '', action)
            return reg_action(action)

        extra_attrs = ""
        if 'on-swipe-left' in node.props: extra_attrs += f' data-swipe-left="{process_action(node.props["on-swipe-left"])}"'
        if 'on-swipe-right' in node.props: extra_attrs += f' data-swipe-right="{process_action(node.props["on-swipe-right"])}"'
        if 'on-context-menu' in node.props: extra_attrs += f' data-context="{process_action(node.props["on-context-menu"])}" oncontextmenu="event.preventDefault(); execAction({{target: this}}, this.getAttribute(\'data-context\'))"'
        snd_val = node.props.get('sound', '').replace('"', '').strip()
        if snd_val: extra_attrs += f' data-sound="{snd_val}"'
        onclick_snd = "window.playSound(this.getAttribute('data-sound'));" if snd_val else ""
        bind_attrs = ""
        if '{' in param:
            for mexpr in re.findall(r'\{([^}]+)\}', param): reg_expr(mexpr)
            bind_attrs = f' data-bind-text="{esc_attr(param)}"'

        inner_html = "".join(process_node(c, component_args, slot_html, component_events) for c in node.children)

        if tag == "For":
            right_raw = param.split(' in ', 1)[1] if ' in ' in param else ''
            reg_expr(right_raw)
            return f'<div class="nara-for" data-for="{esc_attr(param)}" style="display:contents"><template>{inner_html}</template></div>'
        if tag == "If":
            branches_html = f'<div class="nara-ifbranch" data-if="{esc_attr(param)}" style="display:contents">{inner_html}</div>'
            reg_expr(param)
            prevs = [param]
            for cond, blk in node.props.get('_else', []):
                blk_html = "".join(process_node(c, component_args, slot_html, component_events) for c in blk.children)
                neg = ' && '.join([f'!({p})' for p in prevs])
                eff = neg if cond is None else f'{neg} && ({cond})'
                reg_expr(eff)
                branches_html += f'<div class="nara-ifbranch" data-if="{esc_attr(eff)}" style="display:contents">{blk_html}</div>'
                if cond is not None: prevs.append(cond)
            return f'<div class="nara-ifchain" style="display:contents">{branches_html}</div>'
        if tag == "Route":
            trans_cls = {'slide': ' t-slide', 'zoom': ' t-zoom', 'fade': ' t-fade'}.get(node.props.get('transition', '').strip(), '')
            return f'<div class="nara-route{trans_cls}" data-route="{param}">{inner_html}</div>'

        base_class = "nara-element"
        if tag in ["Container", "Row", "Column", "Card", "ScrollBox"]: base_class = f"nara-{tag.lower()}"
        if is_draggable:
            base_class += " nara-draggable"
            if 'left' not in node.props and 'top' not in node.props: css_out += f".{cname} {{ top: 15%; left: 5%; }}\n"

        bind_key = node.props.get('bind', '')
        if tag == "Text": return f'<p class="{base_class} {cname}"{bind_attrs}{extra_attrs}>{param}</p>'
        if tag == "Image":
            bind_src = f' data-bind-src="{esc_attr(param)}"' if '{' in param else ''
            if '{' in param:
                for mexpr in re.findall(r'\{([^}]+)\}', param): reg_expr(mexpr)
            return f'<img src="{param}" class="{base_class} {cname}"{bind_src}{extra_attrs}>'
        if tag == "Button":
            key = process_action(node.props.get('on-click', ''))
            return f'<button class="{base_class} {cname}" data-action="{key}" onclick="{onclick_snd} execAction({{target: this}}, this.getAttribute(\'data-action\'))"{bind_attrs}{extra_attrs}>{param}</button>'
        if tag == "Input": return f'<input type="text" class="{base_class} nara-input {cname}" placeholder="{param}" data-model="{bind_key}" data-model-type="text"{extra_attrs}>'
        if tag == "TextArea": return f'<textarea class="{base_class} nara-textarea {cname}" placeholder="{param}" data-model="{bind_key}" data-model-type="text"{extra_attrs}></textarea>'
        if tag == "Select":
            opts = "".join([f'<option value="{c.props.get("value", c.param)}">{c.param}</option>' for c in node.children if c.name == 'Option'])
            return f'<select class="{base_class} nara-select {cname}" data-model="{bind_key}" data-model-type="text"{extra_attrs}>{opts}</select>'
        if tag == "Option": return ''
        if tag in ("Toggle", "Checkbox"):
            if tag == "Toggle":
                return f'<label class="{base_class} nara-field {cname}"><input type="checkbox" class="nara-toggle-input" data-model="{bind_key}" data-model-type="bool"><span class="nara-toggle-ui"></span><span class="nara-field-label">{param}</span></label>'
            return f'<label class="{base_class} nara-field {cname}"><input type="checkbox" class="nara-checkbox" data-model="{bind_key}" data-model-type="bool"><span class="nara-field-label">{param}</span></label>'
        if tag == "Slider":
            mn, mx, st = node.props.get('min', '0'), node.props.get('max', '100'), node.props.get('step', '1')
            return f'<label class="{base_class} nara-field {cname}"><span class="nara-field-label">{param}</span><input type="range" class="nara-slider" min="{mn}" max="{mx}" step="{st}" data-model="{bind_key}" data-model-type="number"{extra_attrs}></label>'
        if tag == "Icon": return f'<i class="{param} {base_class} {cname}"{bind_attrs}{extra_attrs}></i>'
        action_attr = ""
        if 'on-click' in node.props:
            key = process_action(node.props['on-click'])
            action_attr = f' data-action="{key}" onclick="{onclick_snd} execAction({{target: this}}, this.getAttribute(\'data-action\'))"'
        elif snd_val:
            action_attr = f' onclick="{onclick_snd}"'
        return f'<div class="{base_class} {cname}"{bind_attrs}{extra_attrs}{action_attr}>{inner_html}</div>'

    html_body = "".join(process_node(c) for c in ast_root.children)

    computed_lines = ""
    for c in computed:
        lhs, rhs = c.split('=', 1)[0].strip(), c.split('=', 1)[1].strip()
        reg_expr(rhs)
        computed_lines += f"try {{ state.{lhs} = evalInScope({json.dumps(rhs)}); }} catch(e){{}}\n"
    on_mount_lines = "".join([f"try {{ with(state) {{ {m} }} }} catch(e){{}}\n" for m in on_mounts])

    expr_fns_js = "\n".join(['NARA_EXPRS[' + json.dumps(k) + '] = (state, S) => { with(state) { with(S || {}) { return (' + src + '); } } };' for src, k in expr_keys.items()])
    action_fns_js = "\n".join(['NARA_ACTIONS[' + json.dumps(k) + '] = (state, S) => { with(state) { with(S || {}) { ' + src + ' } } };' for k, src in actions.items()])
    expr_keys_json = json.dumps(expr_keys)

    engine_core = f"""
console.log('{NARA_VERSION} Initialized.');
const naraRoot = document;
let __naraRenders = 0, __naraLastMs = 0;

window.playSound = function(src) {{
    if(!src) return;
    let audio = new Audio(src);
    audio.play().catch(e => console.warn("Audio blocked: ", e));
}};

window.NaraFS = {{
    db: null,
    init() {{
        return new Promise((resolve, reject) => {{
            let req = indexedDB.open('NaraOS_Drive', 1);
            req.onupgradeneeded = e => e.target.result.createObjectStore('Files');
            req.onsuccess = e => {{ this.db = e.target.result; resolve(); }};
            req.onerror = e => reject(e);
        }});
    }},
    async write(filename, content) {{
        if(!this.db) await this.init();
        return new Promise(resolve => {{
            let tx = this.db.transaction('Files', 'readwrite');
            tx.objectStore('Files').put(content, filename);
            tx.oncomplete = () => resolve(true);
        }});
    }},
    async read(filename) {{
        if(!this.db) await this.init();
        return new Promise(resolve => {{
            let tx = this.db.transaction('Files', 'readonly');
            let req = tx.objectStore('Files').get(filename);
            req.onsuccess = () => resolve(req.result || "");
        }});
    }}
}};
NaraFS.init();

function toast(msg) {{
    let t = document.getElementById('nara-toast');
    if(!t) {{ t = document.createElement('div'); t.id = 'nara-toast'; t.className = 'nara-toast-style'; document.body.appendChild(t); }}
    t.innerText = msg; t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 3000);
}}

let touchStartX = 0; let touchEndX = 0;
document.addEventListener('touchstart', e => {{ touchStartX = e.changedTouches[0].screenX; }});
document.addEventListener('touchend', e => {{
    touchEndX = e.changedTouches[0].screenX;
    let el = e.target.closest('[data-swipe-left], [data-swipe-right]');
    if (!el) return;
    if (touchEndX < touchStartX - 60) {{ let act = el.getAttribute('data-swipe-left'); if(act) window.execAction({{target: el}}, act); }}
    if (touchEndX > touchStartX + 60) {{ let act = el.getAttribute('data-swipe-right'); if(act) window.execAction({{target: el}}, act); }}
}});

window.toggleDark = function() {{
    let b = document.body;
    if(b.classList.contains('dark-theme')) {{ b.classList.remove('dark-theme'); b.classList.add('light-theme'); }}
    else if(b.classList.contains('light-theme')) {{ b.classList.remove('light-theme'); b.classList.add('dark-theme'); }}
    else {{ b.classList.add(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'light-theme' : 'dark-theme'); }}
}};

function deepProxy(obj, callback) {{
    if (typeof obj === 'object' && obj !== null) {{
        for (let key in obj) obj[key] = deepProxy(obj[key], callback);
        return new Proxy(obj, {{
            set(target, prop, val) {{
                let old = target[prop];
                target[prop] = deepProxy(val, callback);
                let changed;
                if (val !== null && typeof val === 'object') {{
                    try {{ changed = JSON.stringify(old) !== JSON.stringify(target[prop]); }}
                    catch(e) {{ changed = true; }}
                }} else {{ changed = old !== val; }}
                if (changed) callback();
                return true;
            }},
            deleteProperty(target, prop) {{ delete target[prop]; callback(); return true; }}
        }});
    }}
    return obj;
}}

const persistKeys = [{', '.join(persist_keys)}];
const rawState = {{ {', '.join(states)} }};
persistKeys.forEach(k => {{ let saved = localStorage.getItem('nara_'+k); if(saved) try {{ rawState[k] = JSON.parse(saved); }} catch(e){{}} }});
rawState.routeParams = {{}};

let renderTimeout = null;
function triggerRender() {{ if(renderTimeout) cancelAnimationFrame(renderTimeout); renderTimeout = requestAnimationFrame(updateDOM); }}

const state = deepProxy(rawState, () => {{
    persistKeys.forEach(k => localStorage.setItem('nara_'+k, JSON.stringify(state[k])));
    triggerRender();
}});
window.state = state;

// [V16] Precompiled expressions: NO runtime eval untuk ekspresi yang dikenal (CSP-friendly)
const NARA_EXPRS = {{}};
{expr_fns_js}
const NARA_EXPR_KEYS = {expr_keys_json};
const NARA_ACTIONS = {{}};
{action_fns_js}

function evalInScope(expr, localScope) {{
    localScope = localScope || {{}};
    let key = NARA_EXPR_KEYS[expr];
    let fn = (key !== undefined) ? NARA_EXPRS[key] : null;
    if (!fn) {{
        try {{ fn = new Function('state', 'S', 'with(state) {{ with(S || {{}}) {{ return (' + expr + '); }} }}'); }} catch (e) {{ return ''; }}
    }}
    try {{ return fn(state, localScope); }} catch (e) {{ return ''; }}
}}

window.execAction = function(e, key) {{
    let fn = NARA_ACTIONS[key];
    let localScope = {{}};
    let fw = (e && e.target && e.target.closest) ? e.target.closest('.nara-for-item') : null;
    if (fw && fw.getAttribute('data-scope')) {{ try {{ localScope = JSON.parse(fw.getAttribute('data-scope')); }} catch(err){{}} }}
    if (!fn) {{ toast('Aksi tidak ditemukan: ' + key); return; }}
    try {{ fn(state, localScope); }} catch (err) {{ console.error(err); toast("Error: " + err.message); }}
}};

window.addEventListener('hashchange', () => {{
    let hash = window.location.hash.slice(1) || '/';
    naraRoot.querySelectorAll('.nara-route').forEach(el => {{
        let path = el.getAttribute('data-route'); let match = false;
        if (path.includes(':')) {{
            let rgx = new RegExp('^' + path.replace(/:([^\\/]+)/g, '(?<$1>[^/]+)') + '$');
            let res = hash.match(rgx);
            if (res) {{ match = true; state.routeParams = res.groups; }}
        }} else match = (hash === path);
        match ? el.classList.add('active') : el.classList.remove('active');
    }});
}});

function scopeOf(el) {{
    let fw = el.closest('.nara-for-item');
    if (fw && fw.getAttribute('data-scope')) {{ try {{ return JSON.parse(fw.getAttribute('data-scope')); }} catch(e){{}} }}
    return {{}};
}}

let zIndexCounter = 100;
function initDraggable() {{
    naraRoot.querySelectorAll('.nara-draggable').forEach(el => {{
        if (el.__dragInit) return;
        el.__dragInit = true;
        let isDragging = false, startX, startY, initialX, initialY;
        el.addEventListener('pointerdown', (e) => {{
            if (e.target.closest('button, input, textarea, select, a, .nara-input')) return;
            el.style.zIndex = ++zIndexCounter;
            isDragging = true;
            startX = e.clientX; startY = e.clientY;
            initialX = el.offsetLeft; initialY = el.offsetTop;
            el.setPointerCapture(e.pointerId);
        }});
        el.addEventListener('pointermove', (e) => {{
            if (!isDragging) return;
            el.style.left = (initialX + (e.clientX - startX)) + 'px';
            el.style.top = (initialY + (e.clientY - startY)) + 'px';
        }});
        el.addEventListener('pointerup', (e) => {{
            if (!isDragging) return;
            isDragging = false;
            el.releasePointerCapture(e.pointerId);
        }});
    }});
}}

function updateDOM() {{
    let __t0 = performance.now();
{computed_lines}
    naraRoot.querySelectorAll('.nara-for').forEach(el => {{
        let [left, right] = el.getAttribute('data-for').split(' in ');
        let list = evalInScope(right) || [];
        let sig = JSON.stringify(list);
        if (el.__naraSig === sig) return;
        el.__naraSig = sig;
        let template = el.querySelector('template').innerHTML;
        Array.from(el.children).forEach(c => {{ if(c.tagName !== 'TEMPLATE') c.remove(); }});
        list.forEach((item, index) => {{
            let w = document.createElement('div');
            w.className = 'nara-for-item'; w.style.display = 'contents';
            let sc = {{ [left.trim()]: item, 'index': index }};
            w.setAttribute('data-scope', JSON.stringify(sc));
            w.innerHTML = template;
            el.appendChild(w);
        }});
    }});

    naraRoot.querySelectorAll('.nara-ifchain').forEach(chain => {{
        let shown = false;
        chain.querySelectorAll(':scope > .nara-ifbranch').forEach(br => {{
            let show = false;
            if (!shown) {{ show = !!evalInScope(br.getAttribute('data-if'), scopeOf(br)); if (show) shown = true; }}
            br.style.display = show ? 'contents' : 'none';
        }});
    }});
    naraRoot.querySelectorAll('[data-if]').forEach(el => {{
        if (el.classList.contains('nara-ifbranch')) return;
        el.style.display = evalInScope(el.getAttribute('data-if'), scopeOf(el)) ? 'contents' : 'none';
    }});

    naraRoot.querySelectorAll('[data-bind-text]').forEach(el => {{
        let sc = scopeOf(el);
        let res = el.getAttribute('data-bind-text').replace(/\\{{([^}}]+)\\}}/g, (m, p1) => evalInScope(p1, sc));
        if (el.innerText !== res) el.innerText = res;
    }});

    naraRoot.querySelectorAll('[data-bind-src]').forEach(el => {{
        let sc = scopeOf(el);
        let res = el.getAttribute('data-bind-src').replace(/\\{{([^}}]+)\\}}/g, (m, p1) => evalInScope(p1, sc));
        if (el.src !== res) el.src = res;
    }});

    naraRoot.querySelectorAll('[data-model]').forEach(el => {{
        let key = el.getAttribute('data-model');
        let type = el.getAttribute('data-model-type') || 'text';
        let val = state[key];
        if (type === 'bool') {{ if (el.checked !== !!val) el.checked = !!val; }}
        else {{ let v = (val === undefined || val === null) ? '' : String(val); if (el.value !== v) el.value = v; }}
        if (!el.hasAttribute('data-bound')) {{
            el.setAttribute('data-bound', 'true');
            let evt = (el.tagName === 'SELECT' || type === 'bool') ? 'change' : 'input';
            el.addEventListener(evt, (e) => {{
                if (type === 'bool') state[key] = e.target.checked;
                else if (type === 'number') state[key] = Number(e.target.value);
                else state[key] = e.target.value;
            }});
        }}
    }});

    initDraggable();
    __naraLastMs = performance.now() - __t0;
    __naraRenders++;
}}

triggerRender();
window.dispatchEvent(new Event('hashchange'));
setTimeout(() => {{ {on_mount_lines} }}, 100);

if (location.search.indexOf('nara-debug') !== -1) {{
    let p = document.createElement('div');
    p.style.cssText = 'position:fixed;left:10px;bottom:10px;z-index:999999;background:rgba(0,0,0,.87);color:#4ade80;font:11px monospace;padding:10px 12px;border-radius:10px;max-width:330px;white-space:pre-wrap;pointer-events:none;';
    document.body.appendChild(p);
    setInterval(() => {{
        let s; try {{ s = JSON.stringify(state, null, 1).slice(0, 400); }} catch(e) {{ s = '?'; }}
        p.textContent = 'NARA DEVTOOLS\\nrenders: ' + __naraRenders + ' | last: ' + __naraLastMs.toFixed(2) + 'ms\\nstate:\\n' + s;
    }}, 400);
}}
"""

    js_engine = "<script>" + engine_core + "</script>"
    if is_live:
        js_engine += """<script>setInterval(() => fetch('/nara-live-reload').then(r => r.json()).then(d => { if(window.lastMod && window.lastMod !== d.mod) location.reload(); window.lastMod = d.mod; }).catch(e=>{}), 800);</script>"""

    html = f"<!DOCTYPE html>\n<html lang='id'>\n<head>\n<meta charset='UTF-8'>\n<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n<title>{app_title}</title>\n<link rel='stylesheet' href='https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css'>\n<style>\n{global_css}{css_out}</style>\n</head>\n<body>\n{html_body}\n{js_engine}\n</body>\n</html>"
    parts = {'css': global_css + css_out, 'body': html_body, 'engine': engine_core, 'title': app_title}
    return html, parts

def compile_source(code, filename="app.nui", is_live=False):
    lexer = Lexer(code, filename)
    parser = Parser(lexer.tokenize(), code, filename)
    ast = parser.parse()
    html, parts = generate_code(ast, parser, is_live)
    parts['lint'] = lint_ast(ast, parser.components, filename)
    return html, parts

def parse_and_compile(main_file, is_live=False):
    with open(main_file, 'r') as f: code = f.read()
    html, parts = compile_source(code, main_file, is_live)
    print_lint(parts['lint'], main_file)
    out_file = main_file.replace('.nui', '.html')
    with open(out_file, 'w') as f: f.write(html)
    return out_file

# ==========================================
# 3.5 LINTER (warning compile-time)
# ==========================================
import difflib
CSS_PROPS = {'color','background','background-color','background-image','width','height','min-width','min-height','max-width','max-height','margin','margin-top','margin-bottom','margin-left','margin-right','padding','padding-top','padding-bottom','padding-left','padding-right','font-size','font-weight','font-family','font-style','text-align','text-decoration','text-transform','line-height','letter-spacing','border','border-bottom','border-top','border-left','border-right','border-radius','box-shadow','opacity','display','flex','flex-direction','flex-wrap','justify-content','align-items','align-self','gap','position','top','left','right','bottom','z-index','overflow','overflow-x','overflow-y','cursor','transition','transform','animation','white-space','word-break','object-fit','grid-template-columns','accent-color','filter','backdrop-filter','user-select','pointer-events','resize','vertical-align','list-style','outline'}
SPECIAL_PROPS = {'on-click','on-swipe-left','on-swipe-right','on-context-menu','bind','hover-scale','hover-shadow','hover-bg','sound','draggable','min','max','step','transition','value','size','weight','radius','animate','_args','_else'}
KNOWN_TAGS = {'Container','Row','Column','Card','ScrollBox','Text','Image','Button','Input','TextArea','Select','Option','Toggle','Checkbox','Slider','Icon','For','If','Route','Slot'}
ANIM_VALUES = {'fade-in','fade-in-up','pop-out'}
TRANS_VALUES = {'slide','zoom','fade'}
JS_KEYWORDS = {'let','const','var','function','return','if','else','for','while','do','break','continue','new','delete','typeof','instanceof','in','of','switch','case','default','try','catch','finally','throw','class','extends','super','this','import','export','from','as','async','await','yield','void'}
JS_WHITE = {'Math','Date','JSON','window','document','localStorage','sessionStorage','NaraFS','toast','emit','routeParams','true','false','null','undefined','NaN','parseInt','parseFloat','String','Number','Boolean','Array','Object','fetch','setTimeout','setInterval','clearTimeout','clearInterval','location','history','navigator','event','eval','console','encodeURIComponent','decodeURIComponent','isNaN','Promise','alert','confirm','prompt','screen','Audio','Image','Error'}

def lint_ast(root, components, filename='app.nui'):
    warnings, declared, referenced = [], set(), set()
    def collect_decl(node):
        for c in node.children:
            if c.type in ('State', 'PersistState'): declared.add(c.param.split(':')[0].strip())
            elif c.type == 'Computed': declared.add(c.param.split('=')[0].strip())
            elif c.type == 'Resource':
                m = re.match(r'(\w+)\s*=', c.param)
                if m: declared.update([m.group(1), m.group(1)+'_loading', m.group(1)+'_error'])
            collect_decl(c)
    collect_decl(root)
    for comp in components.values(): collect_decl(comp)
    def scan_expr(expr, scope, line):
        for ident in re.findall(r'(?<![.\w])([A-Za-z_$][A-Za-z0-9_$]*)', expr):
            referenced.add(ident)
            if ident in scope or ident in declared or ident in JS_WHITE or ident in JS_KEYWORDS: continue
            warnings.append((line, f"identifier '{ident}' dalam ekspresi tidak dikenal (bukan state/computed/resource/variabel loop)"))
    def walk(node, scope):
        if node.type == 'ComponentDef':
            scope = scope | set(p.strip() for p in node.param.split(',') if p.strip())
        if node.type == 'ComponentInstance':
            for pk, pv in node.props.items():
                if pk.startswith('on-'): scan_expr(pv, scope, node.line)
                if pk == '_args':
                    for a in (pv if isinstance(pv, list) else []): scan_expr(a, scope, node.line)
            for c in node.children: walk(c, scope)
            return
        if node.type == 'Element':
            tag = node.name
            if tag not in KNOWN_TAGS and tag not in ('ElseIfBlock', 'ElseBlock'):
                close = difflib.get_close_matches(tag, sorted(KNOWN_TAGS), n=1, cutoff=0.72)
                if close: warnings.append((node.line, f"tag '{tag}' tidak dikenal (dianggap <div> biasa) — mungkin maksud '{close[0]}'?"))
            for pk, pv in node.props.items():
                pline = node.props_lines.get(pk, node.line)
                if not isinstance(pv, str): continue
                if pk not in SPECIAL_PROPS and pk not in CSS_PROPS:
                    close = difflib.get_close_matches(pk, sorted(CSS_PROPS | SPECIAL_PROPS), n=1, cutoff=0.72)
                    warnings.append((pline, f"prop '{pk}' tidak dikenal" + (f" — mungkin maksud '{close[0]}'?" if close else "")))
                if pk == 'animate' and pv.strip() not in ANIM_VALUES:
                    warnings.append((pline, f"nilai animate '{pv.strip()}' tidak dikenal (pilihan: fade-in, fade-in-up, pop-out)"))
                if pk == 'transition' and tag == 'Route' and pv.strip() not in TRANS_VALUES:
                    warnings.append((pline, f"transition '{pv.strip()}' tidak dikenal (pilihan: slide, zoom, fade)"))
                if pk == 'bind':
                    referenced.add(pv.strip())
                    if pv.strip() not in declared: warnings.append((pline, f"bind ke state '{pv.strip()}' yang tidak dideklarasikan"))
                for part in pv.split('|')[1:]:
                    mbp = re.match(r'^\s*(\w+):', part)
                    if mbp and not part.strip().startswith('dark:') and mbp.group(1) not in BP:
                        warnings.append((pline, f"breakpoint '{mbp.group(1)}' tidak dikenal (pilihan: sm, md, lg, xl)"))
                if pk.startswith('on-'): scan_expr(pv, scope, pline)
            if tag in ('Text', 'Button', 'Icon', 'Image'):
                for e in re.findall(r'\{([^}]+)\}', node.param): scan_expr(e, scope, node.line)
            if tag == 'If':
                scan_expr(node.param, scope, node.line)
                for cond, blk in node.props.get('_else', []):
                    if cond: scan_expr(cond, scope, node.line)
                    walk(blk, scope)
            if tag == 'For' and ' in ' in node.param:
                lv, right = node.param.split(' in ', 1)
                scan_expr(right, scope, node.line)
                scope = scope | {lv.strip()}
            if tag in ('Input', 'TextArea', 'Select', 'Toggle', 'Checkbox', 'Slider') and 'bind' not in node.props:
                warnings.append((node.line, f"{tag} tanpa prop bind (nilai tidak akan tersimpan)"))
        for c in node.children:
            if node.type == 'Element' and c.name == 'Option': continue
            walk(c, scope)
    walk(root, set())
    for comp in components.values(): walk(comp, set())
    for d in sorted(declared):
        if d not in referenced and not d.endswith(('_loading', '_error')):
            warnings.append((0, f"info: state '{d}' dideklarasikan tetapi tidak pernah dibaca"))
    return warnings

def lint_code(code, filename='test.nui'):
    lexer = Lexer(code, filename)
    parser = Parser(lexer.tokenize(), code, filename)
    ast = parser.parse()
    return lint_ast(ast, parser.components, filename)

def print_lint(warnings, filename):
    for line, msg in warnings:
        loc = f"{filename}:{line}: " if line else ""
        print(f"⚠️  [LINT] {loc}{msg}")

# ==========================================
# 4. BUILD / PWA / EMBED / MINIFY
# ==========================================
def minify_html(html):
    def css_min(m):
        css = re.sub(r'/\*.*?\*/', '', m.group(1), flags=re.S)
        css = re.sub(r'\s+', ' ', css)
        for a, b in [(' {', '{'), ('{ ', '{'), (' }', '}'), ('; }', '}'), (';}', '}'), (', ', ',')]:
            css = css.replace(a, b)
        return '<style>' + css + '</style>'
    html = re.sub(r'<style>(.*?)</style>', css_min, html, flags=re.S)
    html = re.sub(r'<!--.*?-->', '', html, flags=re.S)
    html = re.sub(r'\n\s*\n', '\n', html)
    return html

def do_build(main_file, pwa=False, embed=False, strict=False):
    with open(main_file) as f: code = f.read()
    html, parts = compile_source(code, main_file, False)
    print_lint(parts['lint'], main_file)
    if strict and parts['lint']:
        print("❌ BUILD dibatalkan karena lint warnings (--strict)"); sys.exit(2)
    out = main_file.replace('.nui', '.html')
    if pwa:
        icon = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'><rect width='512' height='512' rx='96' fill='#0f172a'/><text x='256' y='340' font-size='280' text-anchor='middle' fill='#3b82f6' font-family='sans-serif' font-weight='bold'>N</text></svg>"
        manifest = {"name": parts['title'], "short_name": parts['title'][:12], "start_url": os.path.basename(out),
                    "display": "standalone", "background_color": "#0f172a", "theme_color": "#0f172a",
                    "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}
        sw = "const CACHE='nara-cache-v1';self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.clients.claim());self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;e.respondWith(caches.open(CACHE).then(c=>c.match(e.request).then(r=>r||fetch(e.request).then(res=>{c.put(e.request,res.clone());return res;}).catch(()=>c.match('./')))));});"
        with open('manifest.webmanifest', 'w') as f: json.dump(manifest, f)
        with open('sw.js', 'w') as f: f.write(sw)
        with open('icon.svg', 'w') as f: f.write(icon)
        html = html.replace('</head>', '<link rel="manifest" href="manifest.webmanifest"><meta name="theme-color" content="#0f172a"></head>')
        html = html.replace('</body>', '<script>if("serviceWorker" in navigator){navigator.serviceWorker.register("sw.js");}</script></body>')
    min_html = minify_html(html)
    with open(out, 'w') as f: f.write(min_html)
    print(f"✅ BUILD: {out} ({len(html)} B -> {len(min_html)} B, hemat {100*(1-len(min_html)/max(len(html),1)):.0f}%)")
    if pwa: print("✅ PWA: manifest.webmanifest + sw.js + icon.svg (app bisa di-install & offline)")
    if embed:
        engine_embed = parts['engine'].replace('const naraRoot = document;', 'const naraRoot = root;')
        embed_src = ("(function(){\nvar NARA_CSS=" + json.dumps(parts['css']) + ";\nvar NARA_BODY=" + json.dumps(parts['body']) +
                     ";\nvar __st=null;\nfunction ensureStyle(){if(!__st){__st=document.createElement('style');__st.textContent=NARA_CSS;document.head.appendChild(__st);}}\n"
                     "function bootNara(root){ensureStyle();root.innerHTML=NARA_BODY;\n" + engine_embed + "\n}\n"
                     "if(window.customElements&&!customElements.get('nara-app')){customElements.define('nara-app',class extends HTMLElement{connectedCallback(){if(this.__n)return;this.__n=true;bootNara(this);}});}\n"
                     "function tryMount(){var m=document.getElementById('nara-mount');if(m&&!m.__n){m.__n=true;bootNara(m);}}\n"
                     "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',tryMount);}else{tryMount();}\n})();\n")
        with open('embed.js', 'w') as f: f.write(embed_src)
        print("✅ EMBED: embed.js -> pakai <div id='nara-mount'></div> atau <nara-app></nara-app> di halaman/React mana pun")

# ==========================================
# 5. PLAYGROUND / VSCODE / TEMPLATES
# ==========================================
PLAYGROUND_HTML = """<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>NaraUI Playground</title><style>body{margin:0;background:#0f172a;color:#e2e8f0;font-family:monospace;display:flex;flex-direction:column;height:100vh}
header{padding:10px 16px;background:#1e293b;font-weight:bold}#wrap{display:flex;flex:1;min-height:0}
textarea{flex:1;background:#0b1120;color:#7dd3fc;border:none;padding:14px;font:13px monospace;resize:none;outline:none}
#right{flex:1;display:flex;flex-direction:column;border-left:2px solid #1e293b}#err{background:#7f1d1d;color:#fecaca;padding:8px 12px;font:12px monospace;white-space:pre-wrap;display:none}
iframe{flex:1;border:none;background:white}</style></head><body>
<header>NaraUI Playground (v16) - edit kiri, preview kanan</header>
<div id='wrap'><textarea id='src' spellcheck='false'></textarea><div id='right'><div id='err'></div><iframe id='prev'></iframe></div></div>
<script>
var ta=document.getElementById('src'),fr=document.getElementById('prev'),er=document.getElementById('err'),tm=null;
ta.value='App "Halo Playground" {\\n    state: n = 0;\\n    Container {\\n        Text "Klik: {n}" { size: 24px; weight: bold; }\\n        Button "+1" { background: #3b82f6; color: white; on-click: n += 1; }\\n    }\\n}';
function run(){fetch('/compile',{method:'POST',body:ta.value}).then(r=>r.json()).then(d=>{if(d.ok){er.style.display='none';fr.srcdoc=d.html;}else{er.style.display='block';er.textContent=d.error;}});}
ta.addEventListener('input',()=>{clearTimeout(tm);tm=setTimeout(run,600);});run();
</script></body></html>"""

def start_playground():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.send_header('Content-type', 'text/html'); self.end_headers()
            self.wfile.write(PLAYGROUND_HTML.encode())
        def do_POST(self):
            if self.path != '/compile': self.send_response(404); self.end_headers(); return
            code = self.rfile.read(int(self.headers['Content-Length'])).decode('utf-8')
            try:
                html, _ = compile_source(code, 'playground.nui', False)
                resp = json.dumps({'ok': True, 'html': html})
            except NaraCompileError as e:
                resp = json.dumps({'ok': False, 'error': str(e)})
            except Exception as e:
                resp = json.dumps({'ok': False, 'error': traceback.format_exc()[-800:]})
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(resp.encode())
    print("🎮 Playground di http://localhost:8081")
    HTTPServer(('0.0.0.0', 8081), H).serve_forever()

def gen_vscode():
    os.makedirs('naraui-vscode/syntaxes', exist_ok=True); os.makedirs('naraui-vscode/snippets', exist_ok=True)
    pkg = {"name": "naraui-language", "displayName": "NaraUI Language", "version": "1.0.0", "publisher": "NarzX",
           "engines": {"vscode": "^1.70.0"}, "categories": ["Programming Languages"],
           "contributes": {"languages": [{"id": "nui", "aliases": ["NaraUI"], "extensions": [".nui"]}],
                           "grammars": [{"language": "nui", "scopeName": "source.nui", "path": "./syntaxes/naraui.tmLanguage.json"}],
                           "snippets": [{"language": "nui", "path": "./snippets/naraui.json"}]}}
    tm = {"scopeName": "source.nui", "patterns": [
        {"include": "#comments"}, {"include": "#strings"}, {"include": "#keywords"}, {"include": "#numbers"}],
        "repository": {
            "comments": {"patterns": [{"name": "comment.line.nui", "match": "//.*"}, {"name": "comment.block.nui", "begin": "/\\*", "end": "\\*/"}]},
            "strings": {"name": "string.quoted.double.nui", "begin": "\"", "end": "\""},
            "keywords": {"name": "keyword.control.nui", "match": "\\b(App|state|computed|on-mount|import|Route|Component|Slot|For|If|Else|resource|persist)\\b|@persist"},
            "numbers": {"name": "constant.numeric.nui", "match": "\\b[0-9]+(px|%|s|ms)?\\b"}}}
    sn = {"app": {"prefix": "app", "body": ["App \"$1\" {", "    Container {", "        $0", "    }", "}"]},
          "route": {"prefix": "route", "body": ["Route \"$1\" {", "    $0", "}"]},
          "persist": {"prefix": "persist", "body": ["@persist state: $1 = $2;"]}}
    json.dump(pkg, open('naraui-vscode/package.json', 'w'), indent=2)
    json.dump(tm, open('naraui-vscode/syntaxes/naraui.tmLanguage.json', 'w'), indent=2)
    json.dump(sn, open('naraui-vscode/snippets/naraui.json', 'w'), indent=2)
    open('naraui-vscode/README.md', 'w').write("Install: code --install-extension (atau copy folder ke ~/.vscode/extensions). Oleh NarzX - github.com/nezXproject")
    print("✅ Ekstensi VS Code dibuat di folder naraui-vscode/")

TEMPLATES = {
'blank': 'App "Blank App" {\n    Container {\n        Text "Halo NaraUI v16!" { size: 28px; weight: bold; }\n    }\n}\n',
'pos': 'App "Kasir POS" {\n    state: cart = [];\n    state: menu = ["Kopi 15k", "Teh 10k", "Roti 12k"];\n    computed: total = cart.length;\n    Container {\n        Text "KASIR" { size: 28px; weight: bold; }\n        For "m in menu" {\n            Button "{m}" { background: #10b981; color: white; on-click: cart.push(m); }\n        }\n        Text "Total item: {total}" { size: 20px; margin-top: 20px; }\n        If "total > 0" { Text "Siap bayar!" { color: #10b981; } } Else { Text "Keranjang kosong" { color: #94a3b8; } }\n        Button "Reset" { background: #ef4444; color: white; on-click: cart = []; }\n    }\n}\n',
'kiosk': 'App "Kiosk Info" {\n    @persist state: volume = 50;\n    @persist state: mute = false;\n    @persist state: msg = "";\n    Container {\n        Text "PENGATURAN KIOSK" { size: 24px; weight: bold; }\n        Slider "Volume" bind: volume min: 0 max: 100;\n        Toggle "Bisukan" bind: mute;\n        TextArea "Tulis pengumuman..." bind: msg;\n        Text "Volume: {volume} | Mute: {mute}" { color: #94a3b8; }\n    }\n}\n',
'admin': 'App "Admin Dashboard" {\n    resource: users = fetch("https://jsonplaceholder.typicode.com/users?_limit=5");\n    Container {\n        Text "USER LIST" { size: 24px; weight: bold; }\n        If "users_loading" { Text "Memuat user..." { color: #94a3b8; } } Else {\n            For "u in users" {\n                Card { width: 100%; background: #1e293b; margin-bottom: 10px;\n                    Text "{u.name}" { weight: bold; color: white; }\n                    Text "{u.email}" { color: #94a3b8; }\n                }\n            }\n        }\n    }\n}\n',
'profil': 'App "Profil NarzX" {\n    Container {\n        Row { justify-content: center;\n            Button "Home" { on-click: window.location.hash = \'#/\'; }\n            Button "About" { on-click: window.location.hash = \'#/about\'; }\n        }\n        Route "/" transition: fade { Text "Halo, saya NarzX" { size: 32px; weight: bold; } }\n        Route "/about" transition: slide { Text "github.com/nezXproject" { size: 20px; color: #3b82f6; } }\n    }\n}\n'}

def create_template(name):
    src = TEMPLATES.get(name, TEMPLATES['blank'])
    fn = f"{name}.nui"
    with open(fn, 'w') as f: f.write(src)
    print(f"✅ Template '{fn}' dibuat. Jalankan: python nara.py {fn} --watch")

# ==========================================
# 6. DEV SERVER & CLI
# ==========================================
def start_dev_server(nui_file):
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/nara-live-reload':
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({'mod': os.path.getmtime(nui_file)}).encode()); return
            return super().do_GET()
    server = HTTPServer(('0.0.0.0', 8080), Handler)
    print(f"🚀 [{NARA_VERSION}] http://localhost:8080/{nui_file.replace('.nui', '.html')}  (tips: tambah ?nara-debug di URL)")
    def auto_comp():
        last = os.path.getmtime(nui_file)
        try: parse_and_compile(nui_file, True)
        except NaraCompileError as e: print("❌", e); return
        while True:
            time.sleep(1)
            try:
                curr = os.path.getmtime(nui_file)
                if curr != last:
                    print("🔄 Recompiling..."); parse_and_compile(nui_file, True); last = curr
            except NaraCompileError as e: print("❌", e)
            except Exception as e: print(e)
    threading.Thread(target=auto_comp, daemon=True).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nDimatikan.")

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("NaraUI v16 - penggunaan:\n  python nara.py app.nui [--watch]\n  python nara.py app.nui --build [--pwa] [--embed]\n  python nara.py --playground | --vscode | --create pos|kiosk|admin|profil|blank")
    elif args[0] == '--playground': start_playground()
    elif args[0] == '--vscode': gen_vscode()
    elif args[0] == '--create': create_template(args[1] if len(args) > 1 else 'blank')
    else:
        main_file = args[0]
        try:
            if '--watch' in args: start_dev_server(main_file)
            elif '--build' in args: do_build(main_file, '--pwa' in args, '--embed' in args, '--strict' in args)
            else:
                out = parse_and_compile(main_file)
                if '--strict' in args and lint_code(open(main_file, encoding='utf-8').read(), main_file): sys.exit(2)
                print(f"✅ SUKSES: {out}")
        except NaraCompileError as e:
            print("❌", e); sys.exit(1)