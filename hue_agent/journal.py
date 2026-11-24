import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import time

from .protocol import ProtocolError


class Journal:
    """Reserve before BLE writes; an interrupted reservation never replays writes."""
    _MAX_SESSION_SEQUENCES = 256

    def __init__(self, path):
        self.wall_anchor=time.time();self.mono_anchor=time.monotonic()
        self._mem_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._exec_lock = threading.Lock()
        self._executor = None
        self._queries = {}
        self._mem_writes = {}
        self._session_sequences = {}
        self._session_seq_ts = {}
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=PERSIST')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA journal_size_limit=65536')
        self.db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)')
        self.db.commit()
        now = self.retention_now()
        with self.db:
            self.db.execute('UPDATE commands SET created=? WHERE created>?', (now, now))
            self.db.execute('UPDATE sequences SET created=? WHERE created>?', (now, now))
        for row_id, row_hash, row_result, created in self.db.execute(
            "SELECT id, hash, result, created FROM commands WHERE created>? AND hash NOT LIKE 'query:%'",
            (now - 1800,),
        ):
            parsed = json.loads(row_result) if row_result else None
            clamped = min(created, now) if created is not None else now
            self._mem_writes[row_id] = (row_hash, parsed, clamped)
        for session_id, last_seq, created in self.db.execute(
            'SELECT session, last, created FROM sequences WHERE created IS NULL OR created>? ORDER BY created ASC',
            (now - 1800,),
        ):
            clamped = min(created, now) if created is not None else now
            self._record_session_seq_locked(session_id, last_seq, clamped)

    def retention_now(self):
        return self.wall_anchor+time.monotonic()-self.mono_anchor

    def _get_executor(self):
        with self._exec_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hue-journal')
            return self._executor

    def _record_session_seq_locked(self, session_id, sequence, now):
        self._session_sequences.pop(session_id, None)
        self._session_sequences[session_id] = sequence
        self._session_seq_ts.pop(session_id, None)
        self._session_seq_ts[session_id] = now
        while len(self._session_sequences) > self._MAX_SESSION_SEQUENCES:
            evict_key = next((k for k in self._session_sequences if k != session_id), None)
            if evict_key is None:
                break
            del self._session_sequences[evict_key]
            self._session_seq_ts.pop(evict_key, None)

    def _prune_memory_locked(self, now, active_session=None):
        stale_queries = [k for k, v in self._queries.items() if v[2] < now - 30]
        for k in stale_queries:
            del self._queries[k]
        stale_writes = [k for k, v in self._mem_writes.items() if v[2] < now - 1800]
        for k in stale_writes:
            del self._mem_writes[k]
        stale_sessions = [
            k for k, ts in self._session_seq_ts.items()
            if k != active_session and ts < now - 1800
        ]
        for k in stale_sessions:
            self._session_sequences.pop(k, None)
            del self._session_seq_ts[k]
        while len(self._session_sequences) > self._MAX_SESSION_SEQUENCES:
            evict_key = next((k for k in self._session_sequences if k != active_session), None)
            if evict_key is None:
                break
            del self._session_sequences[evict_key]
            self._session_seq_ts.pop(evict_key, None)

    def _lookup_mem_locked(self, command, *, include_pending=True):
        now = self.retention_now()
        self._prune_memory_locked(now, active_session=command.session_id)
        expected=('query:' if command.operation=='query' else '')+command.fingerprint
        entry = self._queries.get(command.command_id)
        if entry is not None:
            if entry[0] != expected:
                raise ProtocolError('COMMAND_ID_CONFLICT')
            if entry[1] is None:
                if not include_pending:
                    return None
                return {'commandId': command.command_id, 'status':'error', 'errorCode':'OUTCOME_UNKNOWN'}
            return dict(entry[1])
        write_entry = self._mem_writes.get(command.command_id)
        if write_entry is not None:
            if write_entry[0] != expected:
                raise ProtocolError('COMMAND_ID_CONFLICT')
            if write_entry[1] is None:
                if not include_pending:
                    return None
                return {'commandId': command.command_id, 'status':'error', 'errorCode':'OUTCOME_UNKNOWN'}
            return dict(write_entry[1])
        return None

    def lookup_fast(self, command, *, include_pending=True):
        with self._mem_lock:
            return self._lookup_mem_locked(command, include_pending=include_pending)

    def _lookup_locked(self, command, *, include_pending=True):
        with self._mem_lock:
            mem_hit = self._lookup_mem_locked(command, include_pending=include_pending)
        if mem_hit is not None:
            return mem_hit
        now = self.retention_now()
        expected=('query:' if command.operation=='query' else '')+command.fingerprint
        row = self.db.execute('SELECT hash,result FROM commands WHERE id=? AND created>?',
                              (command.command_id, now-1800)).fetchone()
        if not row:
            return None
        if row[0] != expected:
            raise ProtocolError('COMMAND_ID_CONFLICT')
        if not row[1]:
            if not include_pending:
                return None
            return {'commandId': command.command_id, 'status':'error', 'errorCode':'OUTCOME_UNKNOWN'}
        return json.loads(row[1])

    def lookup(self, command, *, include_pending=True):
        with self._db_lock:
            return self._lookup_locked(command, include_pending=include_pending)

    def reserve_fast(self, command, *, on_error=None):
        with self._mem_lock:
            cached = self._lookup_mem_locked(command)
            if cached is not None:
                return cached
            mem_seq = self._session_sequences.get(command.session_id)
            if mem_seq is not None and command.sequence <= mem_seq:
                raise ProtocolError('STALE_SEQUENCE')
            now = self.retention_now()
            if command.operation=='query':
                self._prune_memory_locked(now, active_session=command.session_id)
                self._record_session_seq_locked(command.session_id, command.sequence, now)
                while len(self._queries) >= 64:
                    oldest_key = next(iter(self._queries))
                    del self._queries[oldest_key]
                self._queries[command.command_id] = ('query:'+command.fingerprint, None, now)
                return None
            self._prune_memory_locked(now, active_session=command.session_id)
            if len(self._mem_writes) >= 256:
                raise ProtocolError('BUSY')
            self._record_session_seq_locked(command.session_id, command.sequence, now)
            self._mem_writes[command.command_id] = (command.fingerprint, None, now)
        self._get_executor().submit(self._persist_reserve, command, now, on_error)
        return None

    def _persist_reserve(self, command, now, on_error=None):
        try:
            with self._db_lock:
                with self.db:
                    self.db.execute('DELETE FROM commands WHERE created<?', (now-1800,))
                    self.db.execute("DELETE FROM commands WHERE hash LIKE 'query:%' AND created<?",(now-30,))
                    self.db.execute('DELETE FROM sequences WHERE session<>? AND created<?', (command.session_id,now-1800))
                    self.db.execute('INSERT OR REPLACE INTO sequences VALUES (?,?,?)', (command.session_id,command.sequence,now))
                    self.db.execute('INSERT OR IGNORE INTO commands VALUES (?,?,NULL,?)', (command.command_id, command.fingerprint, now))
        except Exception as error:
            if on_error is not None:
                on_error(error)

    def reserve(self, command):
        with self._db_lock:
            cached = self._lookup_locked(command)
            if cached is not None:
                return cached
            now = self.retention_now()
            row = self.db.execute('SELECT last FROM sequences WHERE session=?', (command.session_id,)).fetchone()
            db_seq = row[0] if row else None
            with self._mem_lock:
                mem_seq = self._session_sequences.get(command.session_id)
            floors = [x for x in (db_seq, mem_seq) if x is not None]
            if floors and command.sequence <= max(floors):
                raise ProtocolError('STALE_SEQUENCE')
            if command.operation=='query':
                with self._mem_lock:
                    self._prune_memory_locked(now, active_session=command.session_id)
                    self._record_session_seq_locked(command.session_id, command.sequence, now)
                    while len(self._queries) >= 64:
                        oldest_key = next(iter(self._queries))
                        del self._queries[oldest_key]
                    self._queries[command.command_id] = ('query:'+command.fingerprint, None, now)
                return None
            with self.db:
                self.db.execute('DELETE FROM commands WHERE created<?', (now-1800,))
                self.db.execute("DELETE FROM commands WHERE hash LIKE 'query:%' AND created<?",(now-30,))
                # Keep the active session floor even across wall-clock jumps.
                self.db.execute('DELETE FROM sequences WHERE session<>? AND created<?', (command.session_id,now-1800))
                if self.db.execute("SELECT count(*) FROM commands WHERE hash NOT LIKE 'query:%'").fetchone()[0] >= 256:
                    raise ProtocolError('BUSY')
                self.db.execute('INSERT OR REPLACE INTO sequences VALUES (?,?,?)', (command.session_id,command.sequence,now))
                self.db.execute('INSERT INTO commands VALUES (?,?,NULL,?)', (command.command_id, command.fingerprint, now))
            with self._mem_lock:
                self._prune_memory_locked(now, active_session=command.session_id)
                self._record_session_seq_locked(command.session_id, command.sequence, now)
                self._mem_writes[command.command_id] = (command.fingerprint, None, now)
            return None

    def _finish_db_locked(self, command, result):
        with self.db:
            self.db.execute('UPDATE commands SET result=? WHERE id=?',
                            (json.dumps(result, allow_nan=False), command.command_id))

    def finish(self, command, result):
        if command.operation=='query':
            with self._mem_lock:
                entry = self._queries.get(command.command_id)
                if entry is not None:
                    self._queries[command.command_id] = (entry[0], dict(result), entry[2])
            return
        with self._mem_lock:
            self._mem_writes[command.command_id] = (command.fingerprint, dict(result), self.retention_now())
        with self._db_lock:
            self._finish_db_locked(command, result)

    async def lookup_async(self, command, *, include_pending=True):
        return self.lookup_fast(command, include_pending=include_pending)

    async def reserve_async(self, command):
        return self.reserve_fast(command)

    def schedule_finish(self, command, result, *, on_error=None):
        if command.operation=='query':
            self.finish(command, result)
            return None
        with self._mem_lock:
            self._mem_writes[command.command_id] = (command.fingerprint, dict(result), self.retention_now())
        def _write():
            try:
                self.finish(command, result)
            except Exception as error:
                if on_error is not None:
                    on_error(error)
        return self._get_executor().submit(_write)

    async def finish_async(self, command, result):
        if command.operation=='query':
            self.finish(command, result)
            return
        with self._mem_lock:
            self._mem_writes[command.command_id] = (command.fingerprint, dict(result), self.retention_now())
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._get_executor(), self.finish, command, result)

    def flush(self):
        with self._exec_lock:
            executor = self._executor
        if executor is not None:
            executor.submit(lambda: None).result()

    async def flush_async(self):
        with self._exec_lock:
            executor = self._executor
        if executor is not None:
            await asyncio.get_running_loop().run_in_executor(executor, lambda: None)

    def close(self):
        with self._exec_lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True)
        with self._db_lock:
            self.db.close()
