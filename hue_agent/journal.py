import json
import sqlite3
import time

from .protocol import ProtocolError


class Journal:
    """Reserve before BLE writes; an interrupted reservation never replays writes."""
    def __init__(self, path):
        self.wall_anchor=time.time();self.mono_anchor=time.monotonic()
        self.db = sqlite3.connect(str(path))
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)')
        self.db.commit()

    def retention_now(self):
        return self.wall_anchor+time.monotonic()-self.mono_anchor

    def lookup(self, command):
        row = self.db.execute('SELECT hash,result FROM commands WHERE id=? AND created>?',
                              (command.command_id, self.retention_now()-1800)).fetchone()
        if not row:
            return None
        expected=('query:' if command.operation=='query' else '')+command.fingerprint
        if row[0] != expected:
            raise ProtocolError('COMMAND_ID_CONFLICT')
        return json.loads(row[1]) if row[1] else {
            'commandId': command.command_id, 'status':'error', 'errorCode':'OUTCOME_UNKNOWN'}

    def reserve(self, command):
        cached = self.lookup(command)
        if cached is not None:
            return cached
        with self.db:
            now = self.retention_now()
            self.db.execute('DELETE FROM commands WHERE created<?', (now-1800,))
            self.db.execute("DELETE FROM commands WHERE hash LIKE 'query:%' AND created<?",(now-30,))
            # Keep the active session floor even across wall-clock jumps.
            self.db.execute('DELETE FROM sequences WHERE session<>? AND created<?', (command.session_id,now-1800))
            if command.operation=='query':
                self.db.execute("DELETE FROM commands WHERE id IN (SELECT id FROM commands WHERE hash LIKE 'query:%' ORDER BY created DESC LIMIT -1 OFFSET 63)")
            elif self.db.execute("SELECT count(*) FROM commands WHERE hash NOT LIKE 'query:%'").fetchone()[0] >= 256:
                raise ProtocolError('BUSY')
            row = self.db.execute('SELECT last FROM sequences WHERE session=?', (command.session_id,)).fetchone()
            if row and command.sequence <= row[0]:
                raise ProtocolError('STALE_SEQUENCE')
            self.db.execute('INSERT OR REPLACE INTO sequences VALUES (?,?,?)', (command.session_id,command.sequence,now))
            fingerprint=('query:' if command.operation=='query' else '')+command.fingerprint
            self.db.execute('INSERT INTO commands VALUES (?,?,NULL,?)', (command.command_id, fingerprint, now))
        return None

    def finish(self, command, result):
        with self.db:
            self.db.execute('UPDATE commands SET result=? WHERE id=?',
                            (json.dumps(result, allow_nan=False), command.command_id))

    def close(self):
        self.db.close()
