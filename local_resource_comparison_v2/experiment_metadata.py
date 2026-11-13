"""Capture code hashes and software versions before the measured workload."""
import hashlib
import importlib.metadata
import platform
import subprocess
import sys
from pathlib import Path


def environment_metadata():
    root=Path(__file__).resolve().parent
    versions={}
    for name in ('requests','Flask','psutil'):
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    try:
        result=subprocess.run(['docker','version','--format','{{json .}}'],capture_output=True,text=True,timeout=15)
        docker=dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
    except Exception as exc:docker={'error':repr(exc)}
    return dict(python=sys.version,platform=platform.platform(),dependencies=versions,docker=docker,
                code_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(root.glob('*.py'))},
                operator_notes='Record Docker Desktop resource allocation, power mode, and background load separately.')
