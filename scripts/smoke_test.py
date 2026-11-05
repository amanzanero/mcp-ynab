"""Fresh-resolution smoke test.

Installs a built wheel into a clean venv and drives the real `mcp-ynab` entry point
through an MCP streamable HTTP handshake (the fork serves HTTP, not stdio).

The point of this test is what it deliberately does NOT do: it never touches uv.lock,
never runs `uv sync` or `uv run`. It resolves dependencies from the constraints declared
in pyproject.toml, which is what a user gets and what the locked dev environment can
never show us. mcp-ynab 1.0.3 shipped broken for six days because every check we had ran
against a lockfile pinned to a version users would not resolve (see #21).

Usage:
    python scripts/smoke_test.py
    python scripts/smoke_test.py --resolution lowest-direct
    python scripts/smoke_test.py --wheel dist/mcp_ynab-1.1.0-py3-none-any.whl --no-build

Exits non-zero with the server's stderr attached on any failure.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path

# Bump this when tools are added or removed. A mismatch means either the change was
# intentional (update the number) or registration silently broke (fix the bug).
EXPECTED_TOOL_COUNT = 47

# Cheap canaries across a few domain modules, so a partial registration failure is
# reported as a missing name rather than only as a count that happens to still match.
SENTINEL_TOOLS = {"get_user", "list_plans", "list_accounts", "list_transactions"}

READ_TIMEOUT = 30  # seconds to wait for any single JSON-RPC response


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, echoing it, and fail loudly on a non-zero exit."""
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"command failed with exit {result.returncode}: {' '.join(cmd)}")
    return result


def declared_dependencies(repo: Path) -> list[str]:
    """Read the runtime dependencies declared in pyproject.toml.

    These have to be passed to `uv pip install` explicitly for `--resolution
    lowest-direct` to mean anything. Installing only the wheel makes them transitive,
    and lowest-direct ignores transitive requirements, so the leg silently resolves
    identically to `highest` and tests nothing.
    """
    with (repo / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["dependencies"]


def venv_bin(venv: Path, name: str) -> Path:
    """Resolve an executable inside a venv on either Windows or POSIX layout."""
    if os.name == "nt":
        candidate = venv / "Scripts" / f"{name}.exe"
        return candidate if candidate.exists() else venv / "Scripts" / name
    return venv / "bin" / name


CLIENT_SNIPPET = """
import asyncio, json, sys
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def go(url, token):
    http = httpx2.AsyncClient(headers={"Authorization": "Bearer " + token})
    async with streamable_http_client(url, http_client=http) as (r, w):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            print(json.dumps({"server": init.server_info.name, "tools": [t.name for t in tools]}))

asyncio.run(go(sys.argv[1], sys.argv[2]))
"""

TOKEN = "smoke-test-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def handshake(server_cmd: list[str], python: Path) -> list[str]:
    """Start the streamable HTTP server, wait for /health, then run initialize + tools/list
    with the mcp client from the fresh venv. Returns the tool names."""
    port = free_port()
    env = dict(
        os.environ,
        YNAB_API_KEY="smoke-test-dummy-key",
        MCP_AUTH_TOKEN=TOKEN,
        PORT=str(port),
        PYTHONUNBUFFERED="1",
    )
    proc = subprocess.Popen(
        server_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        deadline = time.time() + READ_TIMEOUT
        while True:
            if proc.poll() is not None:
                raise SystemExit(f"server exited early with code {proc.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                pass
            if time.time() > deadline:
                raise SystemExit(f"timed out after {READ_TIMEOUT}s waiting for /health")
            time.sleep(0.5)
        print("  /health ok")

        result = subprocess.run(
            [str(python), "-c", CLIENT_SNIPPET, f"http://127.0.0.1:{port}/mcp", TOKEN],
            capture_output=True, text=True, timeout=READ_TIMEOUT,
        )
        if result.returncode != 0:
            raise SystemExit(f"MCP client handshake failed:\n{result.stderr[-800:]}")
        info = json.loads(result.stdout.strip().splitlines()[-1])
        print(f"  initialize ok (server: {info['server']})")
        return info["tools"]
    finally:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
        if output and output.strip():
            print("  server output:")
            for line in output.strip().splitlines()[:20]:
                print(f"    {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", help="wheel to install (default: build one)")
    parser.add_argument("--no-build", action="store_true", help="skip uv build")
    parser.add_argument("--python", default="3.13", help="python version for the venv")
    parser.add_argument(
        "--resolution",
        choices=["highest", "lowest-direct"],
        help="pass through to uv pip install to test declared floors",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent

    print("[1/4] locating wheel")
    if not args.no_build:
        run(["uv", "build", "--wheel", "--out-dir", str(repo / "dist")], cwd=repo)
    if args.wheel:
        wheel = Path(args.wheel).resolve()
    else:
        wheels = sorted(
            (repo / "dist").glob("*.whl"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not wheels:
            raise SystemExit("no wheel found in dist/; run without --no-build")
        wheel = wheels[0]
    print(f"  {wheel.name}")

    tmp = Path(tempfile.mkdtemp(prefix="mcp-ynab-smoke-"))
    try:
        venv = tmp / "venv"
        print(f"[2/4] clean venv (python {args.python}), no lockfile")
        run(["uv", "venv", str(venv), "--python", args.python])

        py = venv_bin(venv, "python")
        install = ["uv", "pip", "install", "--python", str(py)]
        if args.resolution:
            install += ["--resolution", args.resolution]
        install.append(str(wheel))
        if args.resolution == "lowest-direct":
            # Promote the declared deps to direct requirements so their floors are
            # what actually gets resolved. See declared_dependencies().
            install += declared_dependencies(repo)
        run(install)

        print("[3/4] resolved versions")
        versions = subprocess.run(
            [
                str(py),
                "-c",
                "import importlib.metadata as m;"
                "print('\\n'.join(f'{d} {m.version(d)}' for d in "
                "['mcp','pydantic','httpx','sqlalchemy','aiosqlite']))",
            ],
            capture_output=True,
            text=True,
        )
        for line in versions.stdout.strip().splitlines():
            print(f"  {line}")

        print("[4/4] streamable HTTP handshake against the installed entry point")
        entry = venv_bin(venv, "mcp-ynab")
        if not entry.exists():
            raise SystemExit(f"entry point not installed at {entry}")
        tools = handshake([str(entry)], py)

        names = set(tools)
        missing = SENTINEL_TOOLS - names
        if missing:
            raise SystemExit(f"tools/list is missing expected tools: {sorted(missing)}")
        if len(tools) != EXPECTED_TOOL_COUNT:
            raise SystemExit(
                f"expected {EXPECTED_TOOL_COUNT} tools, got {len(tools)}. "
                "If this change was intentional, update EXPECTED_TOOL_COUNT."
            )

        print(f"\nPASS: {len(tools)} tools registered and served over streamable HTTP")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
