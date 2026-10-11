#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
McCFO · 麦当劳 MCP 客户端  (麦麦财务官 · 取数层)
====================================================

一个 **零第三方依赖**（仅标准库）的 MCP Streamable HTTP 客户端，
用于直接调用麦当劳中国官方 MCP Server（https://mcp.mcd.cn）。

为什么自己写而不是引 SDK：
  · 参赛作品要求「可运行、可复现」，零依赖 = 任何人 clone 下来就能跑；
  · 麦当劳 MCP 是远程托管的 Streamable HTTP，协议面很窄（initialize /
    tools/list / tools/call），不需要完整 SDK；
  · 便于在客户端层统一处理 401 / 429 / SSE / 会话保持 这些真实会遇到的坑。

协议要点（实测于 2026-10）：
  · 传输：POST https://mcp.mcd.cn，Content-Type: application/json
  · 鉴权：Authorization: Bearer <MCP_TOKEN>
  · Accept 必须同时声明 application/json 与 text/event-stream，
    否则部分网关不会返回正确的内容类型；
  · 服务端可能返回 JSON，也可能返回 SSE（`event: message` / `data: {...}`），
    本客户端两种都能解；
  · 服务端可能为有状态（下发 mcp-session-id），也可能无状态，
    本客户端**有则携带、无则不发**，两种模式都兼容。

用法（作为库）：
    from mcd_mcp import McdMcpClient
    cli = McdMcpClient.from_env()          # 读环境变量 MCD_MCP_TOKEN
    print(cli.call("now-time-info"))
    print(cli.call("query-my-coupons", {"page": "1", "pageSize": "200"}))

用法（作为命令行）：
    python mcd_mcp.py list-tools
    python mcd_mcp.py call now-time-info
    python mcd_mcp.py call order-list --args '{}'

依赖：仅标准库（Python 3.8+）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

__all__ = [
    "McdMcpClient",
    "McdMcpError",
    "McdAuthError",
    "McdRateLimitError",
    "MCP_URL",
]

MCP_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mccfo", "version": "1.1.0"}

# 401 / 429 的提示语，直接面向使用者，避免"报错了但不知道为啥"
AUTH_HINT = (
    "MCP Token 无效或已过期（HTTP 401）。请到 https://open.mcd.cn/mcp "
    "重新激活并复制 Token，然后设置环境变量 MCD_MCP_TOKEN。"
)
RATE_HINT = (
    "触发限流（HTTP 429）。麦当劳 MCP 限制 600 次/分钟，"
    "请稍后重试或降低 --max-orders / --max-products 之类的取数上限。"
)


class McdMcpError(RuntimeError):
    """MCP 调用失败的基类。"""


class McdAuthError(McdMcpError):
    """401：Token 无效/过期/未提供。"""


class McdRateLimitError(McdMcpError):
    """429：触发限流。"""


def _env_token() -> Optional[str]:
    """从环境变量取 Token；同时兼容本仓库 .env 里的写法（见 .env.example）。"""
    for key in ("MCD_MCP_TOKEN", "MCD_TOKEN", "MCP_TOKEN"):
        v = os.environ.get(key)
        if v and v.strip():
            return v.strip()
    # 允许项目根目录下放一个 .env（已被 .gitignore 忽略）
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(os.path.dirname(here), ".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() in ("MCD_MCP_TOKEN", "MCD_TOKEN", "MCP_TOKEN"):
                        v = v.strip().strip('"').strip("'")
                        if v:
                            return v
        except OSError:
            pass
    return None


class McdMcpClient:
    """麦当劳 MCP Streamable HTTP 客户端（零依赖）。"""

    def __init__(
        self,
        token: str,
        url: str = MCP_URL,
        timeout: float = 30.0,
        retries: int = 2,
        verbose: bool = False,
    ) -> None:
        if not token or not str(token).strip():
            raise McdAuthError("缺少 MCP Token。请设置环境变量 MCD_MCP_TOKEN。")
        self.token = str(token).strip()
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self.session_id: Optional[str] = None
        self._id = 0
        self._initialized = False
        self._tools_cache: Optional[List[dict]] = None

    # -- 构造 ---------------------------------------------------------------
    @classmethod
    def from_env(cls, **kw) -> "McdMcpClient":
        tok = _env_token()
        if not tok:
            raise McdAuthError(
                "未找到 MCP Token。请执行：\n"
                "  export MCD_MCP_TOKEN='你的Token'      # macOS/Linux\n"
                "  $env:MCD_MCP_TOKEN='你的Token'        # Windows PowerShell\n"
                "或在项目根目录创建 .env 写入 MCD_MCP_TOKEN=... （.env 已被 gitignore）"
            )
        return cls(tok, **kw)

    # -- 底层 ---------------------------------------------------------------
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _log(self, msg: str) -> None:
        if self.verbose:
            sys.stderr.write(f"[mcp] {msg}\n")
            sys.stderr.flush()

    def _post(self, payload: dict) -> dict:
        """发一个 JSON-RPC 请求，返回解析后的应答对象（可能是批量数组，这里只取单条）。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "mccfo-mcp-client/1.1",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")

        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                    sid = resp.headers.get("mcp-session-id")
                    if sid:
                        self.session_id = sid
                return self._decode(raw)
            except urllib.error.HTTPError as e:
                code = e.code
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                if code == 401:
                    raise McdAuthError(AUTH_HINT) from e
                if code == 429:
                    if attempt < self.retries:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    raise McdRateLimitError(RATE_HINT) from e
                last_err = McdMcpError(f"HTTP {code}：{detail}")
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = McdMcpError(f"网络错误：{e}")
                time.sleep(1.0 * (attempt + 1))

        raise last_err or McdMcpError("请求失败（未知原因）")

    @staticmethod
    def _decode(raw: str) -> dict:
        """把应答体解成 dict：兼容纯 JSON 与 SSE 两种返回。"""
        raw = raw.strip()
        if not raw:
            raise McdMcpError("空响应")
        if raw.startswith("{"):
            obj = json.loads(raw)
            if isinstance(obj, list):  # 批量应答
                obj = obj[0] if obj else {}
            return obj

        # SSE：event: xxx\ndata: {...}\n\n
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, list):
                    obj = obj[0] if obj else {}
                # 只关心带 id 的应答，跳过服务端主动推送的 notification
                if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                    return obj
        raise McdMcpError(f"无法解析响应：{raw[:200]}")

    def _rpc(self, method: str, params: Optional[dict] = None, notify: bool = False) -> dict:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if notify:
            # 通知没有 id，服务端不回内容
            try:
                self._post(payload)
            except McdMcpError:
                pass
            return {}
        payload["id"] = self._next_id()
        res = self._post(payload)
        if "error" in res:
            err = res["error"]
            raise McdMcpError(f"{method} 失败：[{err.get('code')}] {err.get('message')}")
        return res.get("result", {})

    # -- 生命周期 -----------------------------------------------------------
    def initialize(self) -> dict:
        if self._initialized:
            return {}
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        # 通知服务端"客户端已就绪"；某些实现要求这一步
        self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True
        self._log(f"initialized: {result.get('serverInfo')}")
        return result

    # -- 工具 ---------------------------------------------------------------
    def list_tools(self, refresh: bool = False) -> List[dict]:
        self.initialize()
        if self._tools_cache is not None and not refresh:
            return self._tools_cache
        result = self._rpc("tools/list", {})
        self._tools_cache = result.get("tools", [])
        return self._tools_cache

    def tool_names(self) -> List[str]:
        return [t.get("name", "") for t in self.list_tools()]

    def call(self, name: str, args: Optional[dict] = None) -> Any:
        """
        调用一个 MCP 工具，返回"解包后的业务数据"。

        解包规则（按优先级）：
          1. structuredContent   —— 结构化返回，直接用
          2. content[i].text 里是 JSON —— 解析后返回
          3. content[i].text 是纯文本 —— 返回拼接文本
        这样上层拿到的就是 dict / list / str，不用关心 MCP 信封。
        """
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": args or {}})

        if result.get("isError"):
            txt = self._content_text(result)
            raise McdMcpError(f"工具 {name} 返回错误：{txt}")

        sc = result.get("structuredContent")
        if isinstance(sc, dict) and sc:
            return sc.get("result", sc)
        return self._content_json_or_text(result)

    @staticmethod
    def _content_text(result: dict) -> str:
        parts = []
        for c in result.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts).strip()

    def _content_json_or_text(self, result: dict) -> Any:
        txt = self._content_text(result)
        if not txt:
            return result
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            return txt

    # -- 安全网 -------------------------------------------------------------
    def try_call(self, name: str, args: Optional[dict] = None, default: Any = None) -> Any:
        """单个工具失败不影响整体：失败返回 default，并把原因打到 stderr。"""
        try:
            return self.call(name, args)
        except McdAuthError:
            raise  # Token 问题必须上抛，没法降级
        except Exception as e:  # noqa: BLE001
            self._log(f"工具 {name} 调用失败（已跳过）：{e}")
            return default


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _main() -> int:
    import argparse

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="麦当劳 MCP 客户端（零依赖）")
    ap.add_argument("cmd", choices=["list-tools", "call", "ping"])
    ap.add_argument("tool", nargs="?", help="工具名（cmd=call 时必填）")
    ap.add_argument("--args", default="{}", help="工具入参 JSON 字符串")
    ap.add_argument("--token", default=None, help="MCP Token；默认读环境变量 MCD_MCP_TOKEN")
    ap.add_argument("--raw", action="store_true", help="输出原始 MCP 信封")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    try:
        cli = McdMcpClient(a.token, verbose=a.verbose) if a.token else McdMcpClient.from_env(verbose=a.verbose)
    except McdAuthError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        if a.cmd == "ping":
            print(json.dumps(cli.initialize(), ensure_ascii=False, indent=2))
        elif a.cmd == "list-tools":
            tools = cli.list_tools()
            print(f"共 {len(tools)} 个工具：")
            for t in tools:
                print(" -", t.get("name"))
        else:
            if not a.tool:
                print("cmd=call 需要指定工具名", file=sys.stderr)
                return 2
            args = json.loads(a.args) if a.args.strip() else {}
            out = cli.call(a.tool, args)
            if isinstance(out, (dict, list)):
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                print(out)
    except McdMcpError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
