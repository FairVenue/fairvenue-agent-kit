"""Isolated agent adapters. Never imports/connects the Arena SDK or its keys.

``NativeTestnetClient`` speaks the TS2 native TESTNET wire only. A V4 bot key
file is refused ("TS2: create a new bot key"). The older schema6 classes stay
source-closed.
"""

from .client import AgentClient
from .crypto import CryptoPrimitives, MemorySigner
from .protocol import AgentError, build_agent_command_signables
from .transport import HttpTransport
from .key_file import load_v1_agent_key, load_native_testnet_agent_key
from .context import BotContextProvider, build_agent_context_signing_bytes
from .native_testnet import NativeTestnetClient
from .installation import NativeTestnetInstallation, load_native_testnet_installation
from . import ts2

__all__ = ["AgentClient", "AgentError", "CryptoPrimitives", "MemorySigner",
           "HttpTransport", "build_agent_command_signables", "load_v1_agent_key",
           "load_native_testnet_agent_key",
           "BotContextProvider", "build_agent_context_signing_bytes", "NativeTestnetClient",
           "NativeTestnetInstallation", "load_native_testnet_installation", "ts2"]
