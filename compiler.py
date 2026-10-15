"""Tokenized compiler for an explicitly bounded SQL*Loader fixed-width subset."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from functools import lru_cache
import re

@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int
    line: int


def tokenize(source: str) -> list[Token]:
    result = []
    i, line = 0, 1
    while i < len(source):
        start, ln, c = i, line, source[i]
        if c.isspace():
            line += c == '\n'; i += 1; continue
        if source.startswith('--', i):
            stop = source.find('\n', i)
            i = len(source) if stop < 0 else stop
            continue
        if source.startswith('/*', i):
            stop = source.find('*/', i + 2)
            if stop < 0: raise ValueError(f'Unclosed comment at line {line}')
            line += source[i:stop + 2].count('\n'); i = stop + 2; continue
        if c in "\"'":
            quote, buf = c, []; i += 1
            while i < len(source):
                c = source[i]; i += 1
                if c == quote:
                    if i < len(source) and source[i] == quote:
                        buf.append(quote); i += 1
                    else: break
                else: buf.append(c); line += c == '\n'
            else: raise ValueError(f'Unclosed quote at line {ln}')
            result.append(Token('string' if quote == "'" else 'quoted', ''.join(buf), start, i, ln))
            continue
        m = re.compile(r'[A-Za-z_$#][\w$#]*|\d+|!=|<>|<=|>=|.').match(source, i)
        value = m.group(); i += len(value)
        kind = 'word' if value[0].isalpha() or value[0] in '_$#' else 'number' if value.isdigit() else 'symbol'
        result.append(Token(kind, value.upper() if kind == 'word' else value, start, i, ln))
    if len(result) > 100_000: raise ValueError('CTL exceeds 100,000 tokens')
    return result


def word(t: Token, value: str) -> bool:
    return t.kind == 'word' and t.value == value


def closing(ts: list[Token], i: int) -> int:
    depth = 0
    for j in range(i, len(ts)):
        if ts[j].kind != 'symbol': continue
        if ts[j].value == '(': depth += 1
        if ts[j].value == ')':
            depth -= 1
            if depth == 0: return j
    raise ValueError(f'Unclosed parenthesis at line {ts[i].line}')


@dataclass(frozen=True)
class Field:
    name: str
    start: int
    end: int
    dtype: str
    date_mask: str = ''
    nullable: bool = True
    filler: str = ''
    constant: str | None = None
    nullif: str = ''
    defaultif: str = ''
    sql_expression: str | None = None
    source: dict = field(default_factory=dict)
    @property
    def width(self): return max(0, self.end - self.start + 1) if self.start else 0
    def to_dict(self): return {**asdict(self), 'width': self.width, 'datatype': self.dtype}


@dataclass
class Branch:
    table: str
    when: str
    fields: list[Field]
    unsupported: list[str]
    source: dict = field(default_factory=dict)
    mode: str = 'INSERT'
    preserve_blanks: bool = False
    trailing_nullcols: bool = False
    @property
    def minimum_length(self): return max((f.end for f in self.fields), default=0)
    def to_dict(self):
        occupied = sorted((f.start, f.end) for f in self.fields if f.start)
        gaps, cursor = [], 1
        for start, end in occupied:
            if start > cursor: gaps.append({'start': cursor, 'end': start - 1})
            cursor = max(cursor, end + 1)
        return {'target_table': self.table, 'table': self.table, 'condition': self.when, 'when': self.when,
                'fields': [f.to_dict() for f in self.fields], 'unsupported': self.unsupported,
                'source': self.source, 'mode': self.mode, 'minimum_length': self.minimum_length,
                'unmapped_intervals': gaps, 'preserve_blanks': self.preserve_blanks,
                'trailing_nullcols': self.trailing_nullcols}


@dataclass
class Schema:
    branches: list[Branch]
    diagnostics: list[str]
    record_format: str = 'stream'
    record_length: int | None = None
    charset: str | None = None
    mode: str = 'INSERT'
    @property
    def compatibility(self):
        return 'partially_supported' if self.diagnostics or any(b.unsupported for b in self.branches) else 'fully_supported'
    def to_dict(self):
        return {'branches': [b.to_dict() for b in self.branches], 'diagnostics': self.diagnostics,
                'compatibility': self.compatibility, 'record_format': self.record_format,
                'record_length': self.record_length, 'charset': self.charset, 'mode': self.mode}


def fragment(source, ts): return source[ts[0].start:ts[-1].end] if ts else ''


@lru_cache(maxsize=512)
def parse_condition(expression: str):
    ts, i = tokenize(expression), 0
    def operand(reference=False):
        nonlocal i
        if i >= len(ts): raise ValueError('Missing condition operand')
        t = ts[i]; i += 1
        if t.kind == 'quoted' and reference: return ('field', t.value.upper())
        if t.kind in ('string', 'quoted'): return ('literal', t.value)
        if t.value == '(':
            if i + 3 >= len(ts): raise ValueError('Incomplete byte range')
            a, colon, b, end = ts[i:i + 4]; i += 4
            if a.kind != 'number' or colon.value != ':' or b.kind != 'number' or end.value != ')':
                raise ValueError('Invalid byte range')
            if int(a.value) < 1 or int(b.value) < int(a.value): raise ValueError('Invalid condition range')
            return ('range', int(a.value), int(b.value))
        if t.kind == 'number': return ('literal', t.value)
        if t.kind == 'word': return ('blanks',) if t.value == 'BLANKS' else ('field', t.value)
        raise ValueError('Unsupported condition operand')
    def atom():
        nonlocal i
        if i < len(ts) and ts[i].value == '(' and not (i + 2 < len(ts) and ts[i + 1].kind == 'number' and ts[i + 2].value == ':'):
            i += 1; n = conjunction()
            if i >= len(ts) or ts[i].value != ')': raise ValueError('Unclosed condition')
            i += 1; return n
        left = operand(reference=True)
        if i >= len(ts) or ts[i].value not in ('=', '!=', '<>'): raise ValueError('Unsupported conditional operator (supports =, !=, <>)')
        op = ts[i].value; i += 1
        return ('compare', left, op, operand())
    def conjunction():
        nonlocal i
        nodes = [atom()]
        while i < len(ts) and word(ts[i], 'AND'):
            i += 1; nodes.append(atom())
        return ('and', nodes)
    if not ts: return ('true',)
    result = conjunction()
    if i != len(ts): raise ValueError('Unsupported condition syntax: ' + fragment(expression, ts[i:]))
    return result


def evaluate_condition(expression, row, branch, branches, encoding='ascii'):
    try: tree = parse_condition(expression)
    except ValueError: return None
    def get(node):
        if node[0] == 'literal': return node[1]
        if node[0] == 'blanks': return ('blanks',)
        if node[0] == 'range':
            if len(row) < node[2]: return None
            return row[node[1] - 1:node[2]].decode(encoding)
        local = [f for f in branch.fields if f.name == node[1]]
        fields = local or [f for b in branches for f in b.fields if f.name == node[1]]
        if not fields or len({(f.start, f.end, f.constant, f.sql_expression) for f in fields}) != 1: return None
        f = fields[0]
        if f.sql_expression: return None
        if f.constant is not None: return f.constant
        if len(row) < f.end: return None
        val = row[f.start - 1:f.end].decode(encoding)
        return val if branch.preserve_blanks else val.rstrip(' \t')
    def run(node):
        if node[0] == 'true': return True
        if node[0] == 'and':
            results = [run(n) for n in node[1]]
            return False if False in results else None if None in results else True
        a, b = get(node[1]), get(node[3])
        if a is None or b is None: return None
        same = (not str(a).strip(' \t')) if b == ('blanks',) else (not str(b).strip(' \t')) if a == ('blanks',) else a == b
        return same if node[2] == '=' else not same
    try: return run(tree)
    except UnicodeDecodeError: return None


def compile_field(source, ts, cursor):
    if not ts or ts[0].kind not in ('word', 'quoted'): raise ValueError('Invalid field declaration')
    name, i, errors = ts[0].value.upper(), 1, []
    start = end = 0; dtype = 'CHAR'; mask = ''; filler = ''; const = None
    nullif = defaultif = ''; sql = None; width = None
    if i < len(ts) and ts[i].value in ('FILLER', 'BOUNDFILLER'):
        filler = ts[i].value; i += 1
    if i < len(ts) and word(ts[i], 'CONSTANT'):
        i += 1
        if i >= len(ts) or ts[i].kind not in ('string', 'quoted', 'number'): raise ValueError(f'{name}: CONSTANT requires a literal')
        const = ts[i].value; dtype = 'CONSTANT'; i += 1
    else:
        if i < len(ts) and word(ts[i], 'POSITION'):
            i += 1
            if i >= len(ts) or ts[i].value != '(': raise ValueError(f'{name}: invalid POSITION')
            j = closing(ts, i); pos = ts[i + 1:j]; i = j + 1
            k = 0
            if pos and pos[0].value == '*':
                start = cursor; k = 1
                if k + 1 < len(pos) and pos[k].value == '+' and pos[k + 1].kind == 'number':
                    start += int(pos[k + 1].value); k += 2
            elif pos and pos[0].kind == 'number': start = int(pos[0].value); k = 1
            if k + 1 < len(pos) and pos[k].value == ':' and pos[k + 1].kind == 'number':
                end = int(pos[k + 1].value); k += 2
            if not start or k != len(pos): raise ValueError(f'{name}: unsupported POSITION')
        else: start = cursor
        if i < len(ts) and ts[i].kind == 'word':
            dtype = ts[i].value; i += 1
            if dtype in ('INTEGER', 'DECIMAL') and i < len(ts) and word(ts[i], 'EXTERNAL'):
                dtype += ' EXTERNAL'; i += 1
        if dtype not in ('CHAR', 'DATE', 'INTEGER EXTERNAL', 'DECIMAL EXTERNAL'): errors.append(f'{name}: unsupported datatype {dtype}')
        if i < len(ts) and ts[i].value == '(':
            j = closing(ts, i)
            if j != i + 2 or ts[i + 1].kind != 'number': errors.append(f'{name}: unsupported datatype length')
            else: width = int(ts[i + 1].value)
            i = j + 1
        if dtype == 'DATE' and i < len(ts) and ts[i].kind in ('string', 'quoted'):
            mask = ts[i].value.upper(); i += 1
        if dtype == 'DATE' and mask not in ('RRMMDD', 'MMDDRR', 'YYMMDD', 'MMDDYY', 'YYYYMMDD', 'YYYY-MM-DD', 'DD-MON-YYYY', 'DDMMYYYY', 'MM/DD/YYYY'):
            errors.append(f'{name}: unsupported DATE mask {mask or "(implicit NLS mask)"}')
        if not end and not width and dtype in ('INTEGER EXTERNAL', 'DECIMAL EXTERNAL'): errors.append(f'{name}: an explicit numeric byte length is required')
        end = end or start + (width or (len(mask) if dtype == 'DATE' else 1)) - 1
        if start < 1 or end < start or end > 1_000_000: raise ValueError(f'{name}: invalid or excessive byte range')
        if width and width != end - start + 1: errors.append(f'{name}: datatype length differs from POSITION width')
        cursor = end + 1
    while i < len(ts):
        if ts[i].value in ('NULLIF', 'DEFAULTIF') and ts[i].kind == 'word':
            kind = ts[i].value; i += 1; first = i
            while i < len(ts) and ts[i].kind != 'quoted' and ts[i].value not in ('NULLIF', 'DEFAULTIF'): i += 1
            condition = fragment(source, ts[first:i])
            try: parse_condition(condition)
            except ValueError as exc: errors.append(f'{name}: {kind}: {exc}')
            if kind == 'NULLIF': nullif = condition
            else: defaultif = condition
        elif ts[i].kind == 'quoted':
            sql = ts[i].value; errors.append(f'{name}: SQL transformation retained as metadata; Oracle execution required'); i += 1
        else:
            errors.append(f'{name}: unsupported modifier: {fragment(source, ts[i:])}'); break
    f = Field(name, start, end, dtype, mask, True, filler, const, nullif, defaultif, sql,
              {'line': ts[0].line, 'start': ts[0].start, 'end': ts[-1].end, 'declaration': fragment(source, ts)})
    return f, cursor, errors


@lru_cache(maxsize=24)
def compile_ctl(source: str) -> Schema:
    ts = tokenize(source)
    if not ts or len(source) > 2_000_000: raise ValueError('CTL is empty or exceeds 2 MB')
    heads, depth = [], 0
    for i, t in enumerate(ts):
        if t.kind == 'symbol': depth += (t.value == '(') - (t.value == ')')
        if depth < 0: raise ValueError('Unexpected closing parenthesis')
        if depth > 64: raise ValueError('CTL nesting exceeds 64 levels')
        if depth == 0 and i + 1 < len(ts) and word(t, 'INTO') and word(ts[i + 1], 'TABLE'): heads.append(i)
    if depth: raise ValueError('Unbalanced CTL parentheses')
    if not heads: raise ValueError('No INTO TABLE clauses found')
    diagnostics, mode, fmt, length, charset = [], 'INSERT', 'stream', None, None
    prefix, i = ts[:heads[0]], 0
    if len(prefix) < 2 or not any(word(prefix[k], 'LOAD') and word(prefix[k + 1], 'DATA') for k in range(len(prefix) - 1)):
        diagnostics.append('Missing LOAD DATA declaration')
    while i < len(prefix):
        t = prefix[i]
        if t.value in ('LOAD', 'DATA', 'PRESERVE', 'BLANKS', ';'): i += 1
        elif t.kind == 'word' and t.value in ('APPEND', 'INSERT', 'REPLACE', 'TRUNCATE'): mode = t.value; i += 1
        elif t.value in ('INFILE', 'BADFILE', 'DISCARDFILE'):
            i += 1
            if i < len(prefix) and (prefix[i].kind in ('string', 'quoted') or prefix[i].value == '*'): i += 1
            else: diagnostics.append(f'{t.value}: expected filename')
            if i < len(prefix) and prefix[i].kind in ('string', 'quoted'):
                decl = prefix[i].value; i += 1
                m = re.fullmatch(r'FIX\s+(\d+)', decl, re.I)
                if m:
                    fmt, length = 'fixed', int(m[1])
                    if not 1 <= length <= 1_000_000: raise ValueError('FIX record length out of range')
                elif not re.fullmatch(r'STR', decl, re.I): diagnostics.append('Unsupported record format: ' + decl)
        elif t.value == 'CHARACTERSET' and i + 1 < len(prefix): charset = prefix[i + 1].value; i += 2
        elif t.value == 'OPTIONS' and i + 1 < len(prefix) and prefix[i + 1].value == '(':
            j = closing(prefix, i + 1); diagnostics.append('OPTIONS requires Oracle runtime verification: ' + fragment(source, prefix[i:j + 1])); i = j + 1
        else: diagnostics.append(f'Unsupported preamble token at line {t.line}: {t.value}'); i += 1
    preserve = any(word(ts[k], 'PRESERVE') and word(ts[k + 1], 'BLANKS') for k in range(len(ts) - 1))
    branches, cursor = [], 1
    for n, h in enumerate(heads):
        stop = heads[n + 1] if n + 1 < len(heads) else len(ts)
        i = h + 2; table_parts = []
        if i < stop and ts[i].kind in ('word', 'quoted'):
            table_parts.append(ts[i].value); i += 1
            while i + 1 < stop and ts[i].value == '.' and ts[i + 1].kind in ('word', 'quoted'):
                table_parts.extend(['.', ts[i + 1].value]); i += 2
        else: raise ValueError('INTO TABLE requires a table name')
        table = ''.join(table_parts); begin = i; opening = None
        while i < stop:
            if ts[i].value == '(' and ts[i].kind == 'symbol':
                j = closing(ts, i)
                if i + 2 < j and ts[i + 1].kind in ('word', 'quoted') and ts[i + 2].value in ('POSITION', 'FILLER', 'BOUNDFILLER', 'CONSTANT', 'CHAR', 'DATE', 'INTEGER', 'DECIMAL'):
                    opening = i; break
                i = j + 1
            else: i += 1
        if opening is None: raise ValueError(f'Missing recognizable field list for {table}')
        j = closing(ts, opening); header = ts[begin:opening]; wh = next((k for k, t in enumerate(header) if word(t, 'WHEN')), None)
        bmode, errors, when = mode, [], ''
        mods = header if wh is None else header[:wh]
        trailing = any(word(t, 'TRAILING') for t in mods)
        for t in mods:
            if t.value in ('APPEND', 'INSERT', 'REPLACE', 'TRUNCATE'): bmode = t.value
            elif t.value not in ('TRAILING', 'NULLCOLS', 'PRESERVE', 'BLANKS'): errors.append('Unsupported table modifier: ' + t.value)
        if wh is not None:
            when = fragment(source, header[wh + 1:])
            try: parse_condition(when)
            except ValueError as exc: errors.append('WHEN: ' + str(exc))
        parts, last, d = [], opening + 1, 0
        for k in range(opening + 1, j):
            t = ts[k]
            if t.kind == 'symbol':
                d += (t.value == '(') - (t.value == ')')
                if t.value == ',' and d == 0: parts.append(ts[last:k]); last = k + 1
        parts.append(ts[last:j]); fields = []
        first_physical = next((part for part in parts if not any(word(t, 'CONSTANT') for t in part)), [])
        first_relative = bool(first_physical and (any(t.value == '*' for t in first_physical) or not any(word(t, 'POSITION') for t in first_physical)))
        if n and first_relative: errors.append('Relative first field across INTO TABLE branches requires runtime cursor state')
        for part in parts:
            if not part: continue
            f, cursor, errs = compile_field(source, part, cursor); fields.append(f); errors.extend(errs)
        for k, f in enumerate(fields):
            if any(g.name == f.name for g in fields[:k]): errors.append(f'{f.name}: duplicate canonical field name in one layout')
            if not f.start: continue
            if any(g.start and max(g.start, f.start) <= min(g.end, f.end) for g in fields[:k]):
                errors.append(f'{f.name}: overlapping fields within one table layout')
        suffix = ts[j + 1:stop]
        if any(t.value != ';' for t in suffix): errors.append('Unsupported trailing declaration: ' + fragment(source, suffix))
        branches.append(Branch(table, when, fields, errors, {'line': ts[h].line, 'start': ts[h].start, 'end': ts[j].end}, bmode, preserve, trailing))
    return Schema(branches, diagnostics, fmt, length, charset, mode)
