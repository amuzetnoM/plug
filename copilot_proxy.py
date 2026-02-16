"""
Minimal Copilot Proxy
======================
Lightweight OpenAI-compatible proxy for GitHub Copilot API.
Handles token exchange and request forwarding.

Usage:
    python3 copilot_proxy.py          # Start on port 3000
    python3 copilot_proxy.py auth     # Authenticate with GitHub
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from aiohttp import web

COPILOT_API = "https://api.githubcopilot.com"
GITHUB_API = "https://api.github.com"
TOKEN_FILE = Path.home() / ".copilot-proxy" / "token.json"
COPILOT_TOKEN_FILE = Path.home() / ".copilot-proxy" / "copilot_token.json"

# GitHub OAuth App (VS Code Copilot client ID)
COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"


class CopilotAuth:
    """Handle GitHub → Copilot token exchange."""

    def __init__(self):
        self._github_token: str | None = None
        self._copilot_token: str | None = None
        self._copilot_token_expires: float = 0
        self._load_tokens()

    def _load_tokens(self):
        if TOKEN_FILE.exists():
            data = json.loads(TOKEN_FILE.read_text())
            self._github_token = data.get("github_token")
        if COPILOT_TOKEN_FILE.exists():
            data = json.loads(COPILOT_TOKEN_FILE.read_text())
            self._copilot_token = data.get("token")
            self._copilot_token_expires = data.get("expires_at", 0)

    def _save_github_token(self, token: str):
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({"github_token": token}))
        TOKEN_FILE.chmod(0o600)
        self._github_token = token

    def _save_copilot_token(self, token: str, expires_at: int):
        COPILOT_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        COPILOT_TOKEN_FILE.write_text(json.dumps({
            "token": token,
            "expires_at": expires_at,
        }))
        COPILOT_TOKEN_FILE.chmod(0o600)
        self._copilot_token = token
        self._copilot_token_expires = expires_at

    async def device_auth(self):
        """Run GitHub device authorization flow."""
        async with httpx.AsyncClient() as client:
            # Step 1: Request device code
            resp = await client.post(
                "https://github.com/login/device/code",
                json={
                    "client_id": COPILOT_CLIENT_ID,
                    "scope": "copilot",
                },
                headers={"Accept": "application/json"},
            )
            data = resp.json()

            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_uri = data["verification_uri"]
            interval = data.get("interval", 5)

            print(f"\n{'='*50}")
            print(f"  Go to: {verification_uri}")
            print(f"  Enter code: {user_code}")
            print(f"{'='*50}\n")
            print("Waiting for authorization...")

            # Step 2: Poll for token
            while True:
                await asyncio.sleep(interval)
                resp = await client.post(
                    "https://github.com/login/oauth/access_token",
                    json={
                        "client_id": COPILOT_CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )
                result = resp.json()

                if "access_token" in result:
                    self._save_github_token(result["access_token"])
                    print(f"✅ Authenticated successfully!")
                    return result["access_token"]

                error = result.get("error")
                if error == "authorization_pending":
                    continue
                elif error == "slow_down":
                    interval = result.get("interval", interval + 5)
                    continue
                elif error == "expired_token":
                    print("❌ Device code expired. Try again.")
                    return None
                elif error == "access_denied":
                    print("❌ Authorization denied.")
                    return None
                else:
                    print(f"❌ Unexpected error: {error}")
                    return None

    async def get_copilot_token(self) -> str | None:
        """Get a valid Copilot API token, refreshing if needed."""
        # Check if current token is still valid (with 5 min buffer)
        if self._copilot_token and time.time() < (self._copilot_token_expires - 300):
            return self._copilot_token

        if not self._github_token:
            print("No GitHub token. Run: python3 copilot_proxy.py auth")
            return None

        # Exchange GitHub token for Copilot token
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_API}/copilot_internal/v2/token",
                headers={
                    "Authorization": f"token {self._github_token}",
                    "Accept": "application/json",
                    "Editor-Version": "vscode/1.100.0",
                    "Editor-Plugin-Version": "copilot-chat/0.25.0",
                    "User-Agent": "GitHubCopilotChat/0.25.0",
                },
            )

            if resp.status_code != 200:
                print(f"Failed to get Copilot token: {resp.status_code} {resp.text}")
                return None

            data = resp.json()
            token = data.get("token")
            expires_at = data.get("expires_at", int(time.time()) + 1800)

            if token:
                self._save_copilot_token(token, expires_at)
                return token

        return None


class CopilotProxy:
    """OpenAI-compatible proxy server for GitHub Copilot."""

    def __init__(self, port: int = 3000):
        self.port = port
        self.auth = CopilotAuth()
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_post("/v1/chat/completions", self.chat_completions)
        self.app.router.add_post("/chat/completions", self.chat_completions)
        self.app.router.add_get("/v1/models", self.list_models)
        self.app.router.add_get("/models", self.list_models)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_get("/", self.health)

    async def chat_completions(self, request: web.Request) -> web.Response:
        """Proxy chat completions to Copilot API."""
        token = await self.auth.get_copilot_token()
        if not token:
            return web.json_response(
                {"error": {"message": "No valid Copilot token. Run auth first.", "type": "auth_error"}},
                status=401,
            )

        body = await request.json()
        stream = body.get("stream", False)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.25.0",
            "Openai-Organization": "github-copilot",
            "Openai-Intent": "conversation-panel",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            if stream:
                # Streaming response
                response = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    },
                )
                await response.prepare(request)

                async with client.stream(
                    "POST",
                    f"{COPILOT_API}/chat/completions",
                    json=body,
                    headers=headers,
                ) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        await response.write(
                            f"data: {json.dumps({'error': error_body.decode()})}\n\n".encode()
                        )
                        return response

                    async for line in resp.aiter_lines():
                        await response.write(f"{line}\n".encode())

                return response
            else:
                # Non-streaming
                resp = await client.post(
                    f"{COPILOT_API}/chat/completions",
                    json=body,
                    headers=headers,
                )

                return web.Response(
                    body=resp.content,
                    status=resp.status_code,
                    content_type="application/json",
                )

    async def list_models(self, request: web.Request) -> web.Response:
        """Return available models."""
        token = await self.auth.get_copilot_token()
        if not token:
            return web.json_response(
                {"error": "No valid Copilot token"},
                status=401,
            )

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{COPILOT_API}/models",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Copilot-Integration-Id": "vscode-chat",
                },
            )
            return web.Response(
                body=resp.content,
                status=resp.status_code,
                content_type="application/json",
            )

    async def health(self, request: web.Request) -> web.Response:
        has_github = self.auth._github_token is not None
        has_copilot = (
            self.auth._copilot_token is not None
            and time.time() < self.auth._copilot_token_expires
        )
        return web.json_response({
            "status": "ok",
            "github_auth": has_github,
            "copilot_token_valid": has_copilot,
            "port": self.port,
        })

    def run(self):
        print(f"🔌 Copilot Proxy starting on http://localhost:{self.port}")
        print(f"   OpenAI-compatible endpoint: http://localhost:{self.port}/v1/chat/completions")
        if not self.auth._github_token:
            print(f"   ⚠️  No GitHub token. Run: python3 {__file__} auth")
        web.run_app(self.app, host="127.0.0.1", port=self.port, print=None)


async def do_auth():
    auth = CopilotAuth()
    await auth.device_auth()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        asyncio.run(do_auth())
    else:
        port = int(os.environ.get("PORT", "3000"))
        proxy = CopilotProxy(port=port)
        proxy.run()


if __name__ == "__main__":
    main()
