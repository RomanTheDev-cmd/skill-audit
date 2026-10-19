"""Publish complete JSON exclusively; interrupted temporary files are not results."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def value_hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_json(path):
    path = Path(path)
    if '.incomplete-' in path.name:
        raise ValueError('incomplete temporary file is not a finished result')
    return json.loads(path.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-JSON number: ' + value)))


def write_json(path, data, protected_roots=(), protected_files=()):
    path = Path(path).resolve()
    if any(path.is_relative_to(Path(root).resolve()) for root in protected_roots):
        raise ValueError('output must be outside protected source')
    if path in {Path(item).resolve() for item in protected_files}:
        raise ValueError('output must not replace an input')
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.incomplete-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, indent=2).iterencode(data):
                stream.write(chunk)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        # Same-directory hard link publishes complete bytes without overwriting a
        # concurrently created result. No replace/rename of an existing output.
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
