"""Private SQLite admission, quota reservations and source-scoped AI result cache."""
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from bedrock import AIError, env_number


def retry_error(code, message, delay):
    error = AIError(code, message)
    error.retryable = True
    error.retry_after = max(1., math.ceil(delay))
    return error


def reset_seconds(value, now):
    """Groq reset durations (2m59.56s), Retry-After seconds or HTTP dates."""
    if not value: return 0.
    try:
        duration = float(value)
        return max(0., duration) if math.isfinite(duration) else 0.
    except (TypeError, ValueError): pass
    parts = re.findall(r'(\d+(?:\.\d+)?)(ms|s|m|h|d)', str(value))
    if parts and ''.join(a + b for a, b in parts) == str(value):
        return sum(float(a) * {'ms': .001, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}[b] for a, b in parts)
    try: return max(0., parsedate_to_datetime(str(value)).timestamp() - now)
    except (TypeError, ValueError, OverflowError): return 0.


class GroqCoordinator:
    def __init__(self, path=None, clock=None):
        self.path = path
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.db = None

    def limits(self):
        return {'rpm': env_number('GROQ_RPM', 30, 1, 1000000), 'rpd': env_number('GROQ_RPD', 1000, 1, 10000000),
                'tpm': env_number('GROQ_TPM', 8000, 1, 1000000000), 'tpd': env_number('GROQ_TPD', 200000, 1, 10000000000),
                'cache_ttl': env_number('GROQ_CACHE_TTL_SECONDS', 86400, 60, 604800)}

    def connection(self):
        if self.db is not None: return self.db
        path = self.path or os.getenv('GROQ_STATE_DB') or str(Path(os.getenv('STUDIO_DB_PATH') or Path(__file__).parent / '.studio' / 'projects.sqlite3').parent / 'groq.sqlite3')
        if str(path) != ':memory:':
            path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.touch(mode=0o600, exist_ok=True); path.chmod(0o600)
        self.db = sqlite3.connect(str(path), timeout=5, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS groq_calls (
            id TEXT PRIMARY KEY, credential TEXT NOT NULL, created REAL NOT NULL,
            lease_until REAL NOT NULL, tokens INTEGER NOT NULL, state TEXT NOT NULL, cache_key TEXT);
          CREATE INDEX IF NOT EXISTS groq_calls_scope ON groq_calls(credential, created);
          CREATE TABLE IF NOT EXISTS groq_cache (key TEXT PRIMARY KEY, expires REAL NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS groq_limits (
            credential TEXT PRIMARY KEY, cooldown REAL NOT NULL DEFAULT 0,
            requests_left INTEGER, requests_reset REAL, tokens_left INTEGER, tokens_reset REAL);
          CREATE TABLE IF NOT EXISTS groq_observed_limits (credential TEXT PRIMARY KEY, tpm INTEGER, rpd INTEGER);
        ''')
        self.db.commit()
        return self.db

    @contextmanager
    def transaction(self):
        with self.lock:
            db = self.connection()
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def reserve(self, credential, cache_key, tokens, lease_seconds):
        now = self.clock(); limits = self.limits()
        with self.transaction() as db:
            db.execute('DELETE FROM groq_cache WHERE expires<=?', (now,))
            db.execute('DELETE FROM groq_calls WHERE created<=? AND state!=?', (now - 86400, 'active'))
            db.execute("UPDATE groq_calls SET state='abandoned' WHERE state='active' AND lease_until<=?", (now,))
            if cache_key:
                hit = db.execute('SELECT payload FROM groq_cache WHERE key=?', (cache_key,)).fetchone()
                if hit: return {'cached': json.loads(hit[0]), 'reservation': None}
            observed = db.execute('SELECT tpm,rpd FROM groq_observed_limits WHERE credential=?', (credential,)).fetchone()
            if observed:
                if observed[0]: limits['tpm'] = min(limits['tpm'], observed[0])
                if observed[1]: limits['rpd'] = min(limits['rpd'], observed[1])
            if tokens > min(limits['tpm'], limits['tpd']):
                raise AIError('QUOTA_CONFIGURATION', 'This request exceeds the configured token quota; use a smaller batch')
            db.execute('INSERT OR IGNORE INTO groq_limits(credential) VALUES(?)', (credential,))
            cooldown, requests, rreset, remaining, treset = db.execute('SELECT cooldown,requests_left,requests_reset,tokens_left,tokens_reset FROM groq_limits WHERE credential=?', (credential,)).fetchone()
            if cooldown > now: raise retry_error('QUOTA_COOLDOWN', 'Groq is cooling down; analysis remains queued', cooldown - now)
            if requests is not None and rreset and rreset > now and requests < 1:
                raise retry_error('QUOTA_DAILY', 'The Groq request allowance is exhausted; analysis remains queued', rreset - now)
            if remaining is not None and treset and treset > now and remaining < tokens:
                raise retry_error('QUOTA_MINUTE', 'Waiting for the Groq token allowance to reset', treset - now)
            active = db.execute("SELECT lease_until FROM groq_calls WHERE credential=? AND state='active' LIMIT 1", (credential,)).fetchone()
            if active: raise retry_error('AI_BUSY', 'Another Groq request is active; analysis remains queued', min(5., active[0] - now))
            calls = db.execute('SELECT created,tokens FROM groq_calls WHERE credential=? AND created>? ORDER BY created', (credential, now - 86400)).fetchall()
            for window, request_limit, token_limit, code in ((60, limits['rpm'], limits['tpm'], 'QUOTA_MINUTE'), (86400, limits['rpd'], limits['tpd'], 'QUOTA_DAILY')):
                recent = [(stamp, used) for stamp, used in calls if stamp > now - window]
                count, used = len(recent), sum(value for stamp, value in recent)
                for stamp, value in recent:
                    if count < request_limit and used + tokens <= token_limit: break
                    count -= 1; used -= value
                    delay = stamp + window - now
                else: delay = recent[-1][0] + window - now if recent else 0
                if len(recent) >= request_limit or sum(value for stamp, value in recent) + tokens > token_limit:
                    raise retry_error(code, 'Waiting for the shared Groq allowance to reset', delay)
            reservation = secrets.token_hex(16)
            db.execute("INSERT INTO groq_calls VALUES(?,?,?,?,?,'active',?)", (reservation, credential, now, now + lease_seconds, tokens, cache_key))
            db.execute('UPDATE groq_limits SET requests_left=CASE WHEN requests_reset>? THEN requests_left-1 ELSE NULL END,tokens_left=CASE WHEN tokens_reset>? THEN tokens_left-? ELSE NULL END WHERE credential=?', (now, now, tokens, credential))
            return {'cached': None, 'reservation': reservation}

    def finish(self, reservation, run=None, actual_tokens=None, headers=None, status=200):
        now = self.clock(); delay = 0.
        with self.transaction() as db:
            row = db.execute('SELECT credential,cache_key,state FROM groq_calls WHERE id=?', (reservation,)).fetchone()
            if not row or row[2] != 'active': raise AIError('STALE_RESERVATION', 'The Groq request lease expired; retry the queued analysis')
            credential, cache_key, _ = row
            if actual_tokens is not None:
                db.execute("UPDATE groq_calls SET tokens=?,state='done' WHERE id=?", (actual_tokens, reservation))
            else: db.execute("UPDATE groq_calls SET state='failed' WHERE id=?", (reservation,))
            headers = {key.lower(): value for key, value in (headers or {}).items()}
            db.execute('INSERT OR IGNORE INTO groq_observed_limits(credential) VALUES(?)', (credential,))
            for name, column in (('tokens', 'tpm'), ('requests', 'rpd')):
                try: limit = int(headers['x-ratelimit-limit-' + name])
                except (KeyError, ValueError, TypeError): continue
                if limit > 0: db.execute(f'UPDATE groq_observed_limits SET {column}=? WHERE credential=?', (limit, credential))
            for prefix, left, reset in (('requests', 'requests_left', 'requests_reset'), ('tokens', 'tokens_left', 'tokens_reset')):
                try: amount = max(0, int(headers['x-ratelimit-remaining-' + prefix]))
                except (KeyError, ValueError, TypeError): continue
                duration = reset_seconds(headers.get('x-ratelimit-reset-' + prefix), now) or (86400 if prefix == 'requests' else 60)
                db.execute(f'UPDATE groq_limits SET {left}=?,{reset}=? WHERE credential=?', (amount, now + duration, credential))
                if amount == 0: delay = max(delay, duration)
            if status == 429 or status in (500, 502, 503, 504):
                delay = max(delay, reset_seconds(headers.get('retry-after'), now), 1.)
                db.execute('UPDATE groq_limits SET cooldown=MAX(cooldown,?) WHERE credential=?', (now + delay, credential))
            if run is not None and cache_key:
                db.execute('INSERT OR REPLACE INTO groq_cache VALUES(?,?,?)', (cache_key, now + self.limits()['cache_ttl'], json.dumps(run, separators=(',', ':'))))
        return delay

    def close(self):
        with self.lock:
            if self.db is not None: self.db.close(); self.db = None
