#!/usr/bin/env python3
"""Prints, as JSON, the start every sandbox conversation shares: the chat's fixed
prompt and its tools, as the chat sends them to a provider. Run inside a session
at install time (sandbox/install.sh); the service has the model server read it
ahead of the visitors (see "prefix_file" in server.py)."""
import importlib.machinery
import importlib.util
import json
import sys

loader = importlib.machinery.SourceFileLoader("osyr_chat", "/usr/bin/osyr-chat")
spec = importlib.util.spec_from_loader("osyr_chat", loader)
chat = importlib.util.module_from_spec(spec)
loader.exec_module(chat)
json.dump({"system": chat.system_prompt(False), "tools": chat.TOOLS}, sys.stdout)
