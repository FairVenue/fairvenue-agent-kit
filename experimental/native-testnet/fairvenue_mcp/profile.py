"""Owner-private local profile: deployment pins, imported agent keys, continuity.

Layout under ``FAIRVENUE_HOME`` (default ``~/.fairvenue/ts2-testnet``, 0700):

- ``installation.json``: the reviewed public TS2 deployment pins (0600).
- ``keys/<agent>-<delegation>.json``: imported downloaded TS2 bot keys (0600).
- ``continuity/<agent>-<delegation>/original.sqlite``: the SDK's exact-original
  custody, one namespace per key. Never delete it: it may hold the only copy of
  a signed request whose outcome is still unknown.
- ``state.json``: the active key and which continuity namespaces exist.
- ``journal.jsonl``: public summaries of this server's own actions.

Key files are read here and by the SDK only. Their seed never leaves this
process: no tool returns, logs or journals it. A retired V4 key file is refused
by name ("TS2: create a new bot key"), never converted.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fairvenue_schema6 import (
    AgentError,
    CryptoPrimitives,
    NativeTestnetInstallation,
    load_native_testnet_agent_key,
    load_native_testnet_installation,
    ts2,
)

from .config import PACKAGED_INSTALLATION_SHA256, Config
from .market import utc

NATIVE_KEY_SCHEMA = ts2.KEY_SCHEMA
V4_KEY_SCHEMA = ts2.V4_KEY_SCHEMA
V4_INSTALLATION_SCHEMA = "fairvenue-native-testnet-installation-v1"
PACKAGED_PINS = "deployments/arbitrum-sepolia-ts2.json"
KEY_SETUP_PATH = "?setup=api-key#native-agent-key"
MAX_KEY_BYTES = 16_384
STATE_SCHEMA = "fairvenue-mcp-state-v1"
NEW_KEY = "TS2: create a new bot key."
PREVIOUS_DEPLOYMENT = (
    "This key belongs to a previous testnet deployment. Create a new key for the current one."
)
PERMISSION_BITS = (
    (ts2.TRADE, "orders, fee mode and leverage"),
    (ts2.CANCEL, "cancel and cancel all"),
    (ts2.DEAD_MAN_SWITCH, "dead man's switch"),
)
CRYPTO_PROBLEM = (
    "The pinned crypto packages (PyNaCl 1.6.2, pycryptodome 3.24.0, cffi 2.1.1, pycparser 3.0) "
    "are missing or different. Reinstall the server, for example by restarting it through uvx."
)
_AGENT_KEY = re.compile(r"0x[0-9a-f]{64}")
_NONCE = re.compile(r"[0-9]{1,16}")
_NAMESPACE = re.compile(r"([0-9a-f]{64})-([0-9]{1,16})")


class SetupError(Exception):
    """A local setup problem. The message says what to do next."""


def _real(path: Path) -> None:
    if os.path.realpath(path) != str(path):
        raise SetupError(
            f"{path} goes through a symlink. Use its real path {os.path.realpath(path)} instead "
            "(set FAIRVENUE_HOME or the file variables to real paths)."
        )


def private_dir(path: Path) -> None:
    """Create ``path`` and any missing parents with mode 0700, or check an existing one."""
    _real(path)
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SetupError(f"{path} must be a real directory, not a symlink or file.")
    if info.st_uid != os.getuid():
        raise SetupError(f"{path} must be owned by the current user.")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SetupError(f"{path} is readable by other users. Run: chmod 700 '{path}'")


def write_private(path: Path, data: bytes) -> None:
    """Publish ``data`` at ``path`` with mode 0600. Never overwrites a file."""
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path)  # Atomic publish that fails instead of overwriting.
    except FileExistsError:
        raise SetupError(f"{path} already exists and was not overwritten.") from None
    finally:
        os.unlink(temporary)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def crypto_problem() -> str | None:
    try:
        CryptoPrimitives()
    except AgentError:
        return CRYPTO_PROBLEM
    return None


def safe_utc(milliseconds: int | None) -> str | None:
    if milliseconds is None:
        return None
    try:
        return utc(milliseconds)
    except (ValueError, OverflowError, OSError):
        return None


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# State ----------------------------------------------------------------------


def read_state(config: Config) -> dict[str, Any]:
    """The recorded active key and continuity namespaces. Never silently reset."""
    try:
        value = json.loads(config.state_file.read_text())
    except FileNotFoundError:
        return {"schema": STATE_SCHEMA, "active_key": None, "continuity": {}}
    except (OSError, ValueError):
        value = None
    if (
        not isinstance(value, dict)
        or value.get("schema") != STATE_SCHEMA
        or not isinstance(value.get("continuity"), dict)
        or not (value.get("active_key") is None or isinstance(value.get("active_key"), str))
    ):
        raise SetupError(
            f"{config.state_file} is unreadable or has an unexpected format. It records which "
            "continuity databases exist. Inspect or restore it. Never delete it."
        )
    return value


def write_state(config: Config, state: dict[str, Any]) -> None:
    private_dir(config.home)
    temporary = config.state_file.with_name(f".state.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        os.write(descriptor, (json.dumps(state, indent=2, sort_keys=True) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, config.state_file)


# Deployment pins ------------------------------------------------------------


def packaged_installation() -> bytes:
    data = files("fairvenue_schema6").joinpath(PACKAGED_PINS).read_bytes()
    if hashlib.sha256(data).hexdigest() != PACKAGED_INSTALLATION_SHA256:
        raise SetupError(
            "The packaged deployment pins do not match their reviewed hash. Reinstall the kit."
        )
    return data


def _v4_installation(path: Path) -> bool:
    try:
        return bool(json.loads(_read_regular(path)).get("schema") == V4_INSTALLATION_SCHEMA)
    except (SetupError, OSError, ValueError, AttributeError):
        return False


def load_installation(config: Config) -> NativeTestnetInstallation:
    path = config.installation_file
    if not path.exists():
        raise SetupError(
            "Deployment pins are not installed yet. Call install_deployment_pins first."
        )
    _real(path)
    try:
        return load_native_testnet_installation(
            str(path), expected_sha256=config.installation_sha256
        )
    except AgentError:
        if _v4_installation(path):
            raise SetupError(
                f"{path} holds the pins of the retired V4 testnet. This server speaks TS2 and "
                "needs a fresh profile: unset FAIRVENUE_HOME and FAIRVENUE_INSTALLATION_FILE to "
                "use the default ~/.fairvenue/ts2-testnet. Keep the old directory as it is."
            ) from None
        raise SetupError(
            crypto_problem()
            or f"{path} failed verification. It must be the reviewed pin file with SHA256 "
            f"{config.installation_sha256}, owned by you, mode 0600 or 0400 and not a symlink. "
            "Never recompute the expected hash to accept an unexplained file."
        ) from None


def install_pins(config: Config) -> dict[str, Any]:
    path = config.installation_file
    if path.exists():
        installed = load_installation(config)
        return {"status": "already_installed", **pins_view(config, installed)}
    if config.installation_sha256 != PACKAGED_INSTALLATION_SHA256:
        raise SetupError(
            "FAIRVENUE_INSTALLATION_SHA256 names a pin file other than the packaged one. Copy "
            f"that separately reviewed file to {path} yourself (mode 0600)."
        )
    data = packaged_installation()
    if path.parent == config.home:
        private_dir(config.home)
    elif not path.parent.is_dir():
        raise SetupError(f"{path.parent} does not exist. Create it, or unset the file variable.")
    write_private(path, data)
    installed = load_installation(config)
    return {"status": "installed", **pins_view(config, installed)}


def pins_view(config: Config, installed: NativeTestnetInstallation) -> dict[str, Any]:
    pins = installed.policy
    return {
        "file": str(config.installation_file),
        "sha256": installed.sha256,
        "protocol": "TS2",
        "api_url": installed.api_url,
        "chain_id": pins["chain_id"],
        "settlement": pins["settlement"],
        "config_hash": pins["config_hash"],
        "key_setup_url": installed.api_url + KEY_SETUP_PATH,
    }


# Agent keys -----------------------------------------------------------------


def _milliseconds(value: object) -> int | None:
    if isinstance(value, str) and _NONCE.fullmatch(value):
        number = int(value)
        if number <= (1 << 53) - 1:
            return number
    return None


def _material(grant: object) -> dict[str, Any]:
    """The delegation material of a TS2 grant ``{material, signature}``, or ``{}``."""
    material = grant.get("material") if isinstance(grant, dict) else None
    return material if isinstance(material, dict) else {}


def _grant_view(grant: dict[str, Any]) -> dict[str, Any]:
    """Public fields of a TS2 grant. Never the internal account number."""
    material = _material(grant)
    issued, expires = (
        _milliseconds(material.get("issued_at_ms")),
        _milliseconds(material.get("expires_at_ms")),
    )
    permissions = material.get("permissions")
    key = material.get("agent_public_key")
    owner = material.get("owner")
    return {
        "agent_public_key": key if isinstance(key, str) and _AGENT_KEY.fullmatch(key) else None,
        "owner": owner if isinstance(owner, str) and len(owner) <= 42 else None,
        "permissions": [
            label
            for bit, label in PERMISSION_BITS
            if isinstance(permissions, int) and permissions & bit
        ],
        "issued_at": safe_utc(issued),
        "expires_at": safe_utc(expires),
        "expired": expires is None or expires <= time.time() * 1000,
        "can_withdraw": False,
    }


def describe_key(data: bytes, installed: NativeTestnetInstallation | None) -> dict[str, Any]:
    """Public fields of a candidate key file. The seed is never returned."""
    try:
        value = json.loads(data)
    except ValueError:
        return {"kind": "not_json"}
    if not isinstance(value, dict):
        return {"kind": "not_json"}
    if value.get("schema") == V4_KEY_SCHEMA:
        return {"kind": "v4", "note": NEW_KEY}
    if value.get("schema") != NATIVE_KEY_SCHEMA:
        return {
            "kind": "not_native",
            "note": "Not a TS2 bot key (V0 and V4 keys are not accepted). " + NEW_KEY,
        }
    grant = value.get("delegation")

    def text(key: str) -> str | None:
        item = value.get(key)
        return item if isinstance(item, str) and len(item) <= 80 else None

    view: dict[str, Any] = {
        "kind": "native_testnet",
        "settlement": text("settlement"),
        "config_hash": text("config_hash"),
        "api_url": text("api_url"),
        **(_grant_view(grant) if isinstance(grant, dict) else {}),
    }
    if installed is not None:
        current = (
            value.get("settlement") == installed.settlement
            and value.get("config_hash") == installed.config_hash
            and value.get("api_url") == installed.api_url
        )
        view["matches_installed_deployment"] = current
        if not current:
            view["note"] = PREVIOUS_DEPLOYMENT
    return view


def _read_regular(path: Path) -> bytes:
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise SetupError(f"{path} must be a regular file you own (not a symlink).")
    if not 0 < info.st_size <= MAX_KEY_BYTES:
        raise SetupError(f"{path} is not an agent key file (unexpected size).")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        data = os.read(descriptor, MAX_KEY_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) != info.st_size:
        raise SetupError(f"{path} changed while it was read.")
    return data


def _candidates(config: Config, extra_dirs: list[str]) -> list[tuple[float, dict[str, Any]]]:
    """``(modified time, public view)`` of every native key file in the download folders."""
    try:
        installed: NativeTestnetInstallation | None = load_installation(config)
    except SetupError:
        installed = None
    imported = (
        {path.name for path in config.keys_dir.glob("*.json")}
        if config.keys_dir.is_dir()
        else set()
    )
    directories = [
        *config.download_dirs,
        *(Path(os.path.abspath(os.path.expanduser(d))) for d in extra_dirs),
    ]
    found: list[tuple[float, dict[str, Any]]] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("fairvenue*.json")):
            try:
                data = _read_regular(path)
                view = describe_key(data, installed)
                if view["kind"] != "native_testnet":
                    continue
                grant_key = view.get("agent_public_key")
                modified = os.lstat(path).st_mtime
                found.append(
                    (
                        modified,
                        {
                            "path": str(path),
                            "downloaded_at": safe_utc(int(modified * 1000)) or "",
                            "already_imported": isinstance(grant_key, str)
                            and any(name.startswith(grant_key[2:18]) for name in imported),
                            **view,
                        },
                    )
                )
            except (SetupError, OSError, ValueError, TypeError, OverflowError):
                continue  # One odd file never hides the others.
    found.sort(key=lambda item: item[0], reverse=True)
    return found


def find_key_downloads(config: Config, extra_dirs: list[str]) -> list[dict[str, Any]]:
    return [view for _, view in _candidates(config, extra_dirs)]


def fresh_download(config: Config, since: float) -> str | None:
    """The newest usable key downloaded at or after ``since`` (a POSIX time), if any."""
    for modified, view in _candidates(config, []):
        if (
            modified >= since
            and view.get("matches_installed_deployment")
            and not view.get("expired")
            and not view.get("already_imported")
        ):
            return str(view["path"])
    return None


def _key_name(grant: dict[str, Any]) -> tuple[str, str]:
    """``(key file name, continuity namespace)`` for a validated public grant."""
    material = _material(grant)
    key, nonce = material.get("agent_public_key"), material.get("nonce")
    if (
        not isinstance(key, str)
        or not _AGENT_KEY.fullmatch(key)
        or not isinstance(nonce, str)
        or not _NONCE.fullmatch(nonce)
    ):
        raise SetupError("The key has an invalid public grant. Create a new key.")
    return f"{key[2:18]}-{nonce}.json", f"{key[2:]}-{nonce}"


def import_key(config: Config, source: str, *, remove_source: bool = False) -> dict[str, Any]:
    """Copy a downloaded key into the profile and make it active.

    With ``remove_source`` the download is deleted afterwards, but only when it
    is byte-identical to the private copy, so the key is moved, never lost.
    """
    installed = load_installation(config)
    path = Path(os.path.normpath(os.path.abspath(os.path.expanduser(source))))
    data = _read_regular(path)
    view = describe_key(data, installed)
    if view["kind"] == "v4":
        raise SetupError(f"{path} is a V4 key, which cannot sign on TS2. {NEW_KEY}")
    if view["kind"] != "native_testnet":
        raise SetupError(f"{path} is not a TS2 bot key. {NEW_KEY}")
    if not view.get("matches_installed_deployment"):
        raise SetupError(view.get("note", "This key does not match the installed deployment."))
    grant = json.loads(data).get("delegation")
    if not isinstance(grant, dict):
        raise SetupError(f"{path} has no valid public grant. Create a new key.")
    name, namespace = _key_name(grant)
    waiting = pending_originals(config)
    if any(item["namespace"] != namespace for item in waiting):
        raise SetupError(
            "Another key has a signed request whose outcome is unknown. Call "
            "recover_pending_request before switching keys. Nothing was imported."
        )
    recovery_only = bool(view.get("expired"))
    if recovery_only and not any(item["namespace"] == namespace for item in waiting):
        raise SetupError(f"This key expired at {view.get('expires_at')}. Create a new key.")
    private_dir(config.keys_dir)
    target = config.keys_dir / name
    if target.parent != config.keys_dir:
        raise SetupError("The key file name is invalid. Create a new key.")
    if target.exists():
        if _read_regular(target) != data:
            raise SetupError(
                f"{target} already exists with different content. Nothing was overwritten."
            )
        status = "already_imported"
    else:
        write_private(target, data)
        status = "imported"
        try:
            load_native_testnet_agent_key(
                str(target), policy=installed.policy, approved_api_url=installed.api_url
            )
        except AgentError:
            target.unlink()
            raise SetupError(
                crypto_problem()
                or "The key failed local verification (its seed, grant or pins do not match). "
                "Create a fresh key for the current deployment."
            ) from None
    state = read_state(config)
    state["active_key"] = target.name
    write_state(config, state)
    removed = False
    if remove_source and _read_regular(target) == data:
        try:
            if _read_regular(path) == data:
                path.unlink()
                removed = True
        except (SetupError, OSError):
            removed = False
    result: dict[str, Any] = {
        "status": status,
        "file": str(target),
        **({"download_removed": str(path)} if removed else {"download_left_in_place": str(path)}),
        **{
            key: value
            for key, value in view.items()
            if key not in ("kind", "matches_installed_deployment")
        },
    }
    if recovery_only:
        result["note"] = "Expired: usable only to recover its pending request."
    if config.key_file is not None:
        result["warning"] = (
            f"FAIRVENUE_KEY_FILE is set, so the server keeps using {config.key_file} instead."
        )
    return result


def active_key_path(config: Config) -> Path:
    if config.key_file is not None:
        return config.key_file
    name = read_state(config).get("active_key")
    if isinstance(name, str):
        path = config.keys_dir / name
        if "/" in name or not name.endswith(".json") or not path.exists():
            raise SetupError(
                f"The active key file {path} is missing. Import a key again with "
                "import_agent_key. Never delete key files that may have pending requests."
            )
        return path
    keys = sorted(config.keys_dir.glob("*.json")) if config.keys_dir.is_dir() else []
    if len(keys) == 1:
        return keys[0]
    if keys:
        raise SetupError(
            "Several keys are imported and none is active. Import the one to use again."
        )
    raise SetupError(
        "No agent key is imported yet. Ask the user to create one, then call "
        "find_agent_key_downloads and import_agent_key."
    )


def load_key(installed: NativeTestnetInstallation, path: Path) -> tuple[Any, dict[str, Any]]:
    if not path.exists():
        raise SetupError(f"The key file {path} does not exist.")
    _real(path)
    try:
        signer, grant = load_native_testnet_agent_key(
            str(path), policy=installed.policy, approved_api_url=installed.api_url
        )
    except AgentError as error:
        if error.code == "v4_key":
            raise SetupError(f"{path} is a V4 key. {NEW_KEY}") from None
        if error.code == "previous_deployment":
            raise SetupError(f"{path}: {PREVIOUS_DEPLOYMENT}") from None
        raise SetupError(
            crypto_problem()
            or f"{path} failed verification. It must be a TS2 bot key for the installed "
            "deployment, owned by you with mode 0600 or 0400, and not a symlink."
        ) from None
    _key_name(grant)
    return signer, grant


def load_active_key(
    config: Config, installed: NativeTestnetInstallation
) -> tuple[Any, dict[str, Any], Path]:
    path = active_key_path(config)
    signer, grant = load_key(installed, path)
    return signer, grant, path


def key_view(grant: dict[str, Any], path: Path) -> dict[str, Any]:
    return {"file": str(path), **_grant_view(grant)}


def key_for_namespace(config: Config, namespace: str) -> Path | None:
    """The imported key file of a continuity namespace, if it is still in the profile."""
    match = _NAMESPACE.fullmatch(namespace)
    if match is None:
        return None
    if config.key_file is not None and namespace_of_file(config.key_file) == namespace:
        return config.key_file
    path = config.keys_dir / f"{match.group(1)[:16]}-{match.group(2)}.json"
    return path if path.exists() else None


def namespace_of_file(path: Path) -> str | None:
    try:
        grant = json.loads(_read_regular(path)).get("delegation")
        return _key_name(grant)[1] if isinstance(grant, dict) else None
    except (SetupError, OSError, ValueError):
        return None


# Continuity -----------------------------------------------------------------


def custody_location(config: Config, grant: dict[str, Any]) -> Path:
    """Where this key's custody database lives. Creates nothing."""
    return config.continuity_root / _key_name(grant)[1] / "original.sqlite"


def _family(custody: Path) -> list[Path]:
    return [Path(f"{custody}{suffix}") for suffix in ("", ".lock", "-wal", "-shm", "-journal")]


def prepare_continuity(config: Config, grant: dict[str, Any]) -> tuple[Path, bool]:
    """The key's custody database and whether it must be initialized now.

    A namespace is recorded in ``state.json`` before it is initialized, so a
    recorded namespace whose database later disappears is never reinitialized.
    """
    custody = custody_location(config, grant)
    namespace = custody.parent
    state = read_state(config)
    recorded = state["continuity"].get(namespace.name)
    if custody.exists():
        if recorded is None:
            record_continuity(config, custody, grant)
        return custody, False
    if recorded is not None:
        raise SetupError(
            f"The continuity database {custody} is missing, but it was initialized at "
            f"{recorded.get('initialized_at', 'an earlier time')}. It may hold an unresolved "
            "signed request. Restore it and never reinitialize it. If nothing can be pending, "
            "create a new key."
        )
    private_dir(config.home)
    private_dir(config.continuity_root)
    private_dir(namespace)
    record_continuity(config, custody, grant)
    return custody, True


def rollback_continuity(config: Config, custody: Path) -> None:
    """Forget a namespace whose initialization created nothing at all."""
    if any(os.path.lexists(path) for path in _family(custody)):
        return
    state = read_state(config)
    state["continuity"].pop(custody.parent.name, None)
    write_state(config, state)


def record_continuity(config: Config, custody: Path, grant: dict[str, Any]) -> None:
    state = read_state(config)
    state["continuity"][custody.parent.name] = {
        "initialized_at": _now(),
        "agent_public_key": grant["material"]["agent_public_key"],
    }
    write_state(config, state)


def custody_held_elsewhere(custody: Path) -> bool:
    """Whether another process holds this custody database's lock right now."""
    try:
        descriptor = os.open(f"{custody}.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def pending_original(custody: Path) -> dict[str, Any] | None:
    """Public fields of the original retained in one custody database (read-only).

    Raises when the database cannot be read, so callers fail closed.
    """
    if not custody.exists():
        return None
    connection = sqlite3.connect(f"file:{quote(str(custody), safe='/')}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT pending FROM original WHERE singleton=1").fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("custody without its singleton row")
    if row[0] is None:
        return None
    request = json.loads(row[0])["request"]
    action = request["action"]
    if not isinstance(action, dict) or not isinstance(action.get("type"), str):
        raise ValueError("custody with an unexpected original")
    # Public action fields only. The signature and account word stay in custody.
    return {
        "action": {key: value for key, value in action.items() if key != "delegation_nonce"},
        "nonce": action.get("command_nonce") or action.get("client_nonce"),
        "expires_at": safe_utc(_milliseconds(action.get("expires_at_ms"))),
        "request_sha256": ts2.request_sha256(request),
    }


def pending_originals(config: Config) -> list[dict[str, Any]]:
    """Every continuity namespace that holds a request whose outcome is unknown."""
    root = config.continuity_root
    names = set(read_state(config)["continuity"]) if config.state_file.exists() else set()
    if root.is_dir():
        names |= {path.name for path in root.iterdir() if path.is_dir()}
    waiting: list[dict[str, Any]] = []
    for name in sorted(names):
        match = _NAMESPACE.fullmatch(name)
        custody = root / name / "original.sqlite"
        try:
            pending = pending_original(custody)
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            pending = {"unreadable": True}  # Fail closed: treat it as unresolved.
        if pending:
            waiting.append(
                {
                    "namespace": name,
                    "agent_public_key": "0x" + match.group(1) if match else None,
                    "custody": str(custody),
                    **pending,
                }
            )
    return waiting
