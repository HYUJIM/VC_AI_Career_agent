"""Read the project's positional PostgreSQL seed INSERTs without executing SQL."""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from src.clients.backend_client import validate_subject_did

INSERT = re.compile(r'INSERT INTO public\.(users|credentials) VALUES \((.*)\);')
VALUE = re.compile(r"\s*('(?:[^']|'')*'|NULL)\s*(,|$)")


def _values(text):
    values, offset = [], 0
    while offset < len(text):
        match = VALUE.match(text, offset)
        if match is None:
            raise ValueError('Unsupported SQL value; expected quoted text or NULL')
        token, separator = match.groups()
        values.append(None if token == 'NULL' else token[1:-1].replace("''", "'"))
        offset = match.end()
        if not separator:
            return values
        if offset == len(text):
            raise ValueError('Trailing SQL comma')
    raise ValueError('Empty SQL row')


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('Missing seed UUID')
    return str(UUID(value))


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing issuance timestamp')
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError('Seed issuance timestamp must include timezone')
    return instant


def extract_seed(path, subject_did):
    """Return one careers response plus provenance; reject ambiguous identities.

    Supports this repository's single-line, positional INSERT dump only.
    Dump setup directives are ignored, never executed. COPY, expressions,
    explicit column lists, multiline rows and other mutations are rejected.
    """
    validate_subject_did(subject_did)
    path = Path(path)
    raw = path.read_bytes()
    users, credentials, dids, record_ids = {}, [], set(), set()
    for number, original in enumerate(raw.decode('utf-8-sig').splitlines(), 1):
        line = original.strip()
        if not line or line.startswith(('--', '\\restrict ', '\\unrestrict ',
                                         'SET ', 'SELECT pg_catalog.set_config(',
                                         'SELECT pg_catalog.setval(')):
            continue
        match = INSERT.fullmatch(line)
        if match is None:
            raise ValueError(f'Unsupported seed SQL at line {number}')
        table, text = match.groups()
        row = _values(text)
        if len(row) != (6 if table == 'users' else 14):
            raise ValueError(f'Unexpected {table} column count at line {number}')
        if table == 'users':
            uid = _uuid(row[0])
            did = validate_subject_did(row[3])
            if uid in users or did in dids:
                raise ValueError('Duplicate seed user UUID or DID')
            users[uid] = row
            dids.add(did)
        else:
            rid = _uuid(row[0])
            _uuid(row[1])
            if rid in record_ids:
                raise ValueError('Duplicate seed record UUID')
            record_ids.add(rid)
            credentials.append(row)
    if any(_uuid(row[1]) not in users for row in credentials):
        raise ValueError('Seed credential references an unknown user')
    matches = [uid for uid, row in users.items() if row[3] == subject_did]
    if not matches:
        raise ValueError('Requested DID not found in SQL seed')
    uid = matches[0]
    user = users[uid]
    selected = [row for row in credentials if _uuid(row[1]) == uid]
    # Match BE's issued_at DESC; use record ID to make timestamp ties stable.
    selected.sort(key=lambda row: (-_timestamp(row[9]).timestamp(), row[0]))
    rows = []
    for row in selected:
        if row[12] is None:
            raise ValueError('Seed credential has no raw_data')
        rows.append({
            'record_id': row[0], 'issuer': row[6], 'achievement_name': row[5],
            'status': row[8], 'issued_at': _timestamp(row[9]).isoformat(),
            'raw_data': json.loads(row[12]),
        })
    payload = {
        'user_profile': {'id': user[0], 'email': user[1], 'name': user[2], 'did': user[3]},
        'verified_credentials': rows,
    }
    report = {
        'reader_version': 'be-sql-seed/1.0', 'mode': 'sql_seed',
        'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(),
        'byte_count': len(raw), 'total_users': len(users), 'total_credentials': len(credentials),
        'subject_did': subject_did, 'selected_count': len(rows),
        'selected_record_ids': [row['record_id'] for row in rows],
        'source_provider_counts': dict(Counter(row[2] for row in selected)),
        'status_counts': dict(Counter(row['status'] for row in rows)),
        'verification_basis': 'backend_status_only',
        'cryptographic_verification_performed': False,
        'limitations': ['Seed SQL was read, not executed; no live BE or DB check.',
                        'Projection matches current careers API; status is not signature evidence.'],
    }
    return payload, report
