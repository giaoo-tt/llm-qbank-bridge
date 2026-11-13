#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 题库桥 (LLM Qbank Bridge)
=============================
把「大模型 / 知识库 API」包装成一个油猴答题脚本可以直接调用的题库开放接口。

协议完全对齐脚本使用的：
    POST /api/open/v1/search/exact
    Header: X-Consumer-Token: <本地访问密钥>
    Body:   { "stem": 题干, "options": ["选项1", ...], "type": "0|1|3" }
    Resp:   { "success": true, "data": { "items": [ { "stem": ..., "answer": { "kind": "text", "values": [...] } } ] },
              "error": null, "meta": { "billing_result": "ok" } }

只使用 Python 标准库，不需要 pip install 任何东西。
界面通过本机 Edge / Chrome 的 --app 模式打开，看起来就是一个独立小窗口。
"""

from __future__ import annotations

import concurrent.futures
import difflib
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlparse, parse_qs

APP_VERSION = "1.0.0"
# 打包成 exe 后：可写数据放 exe 旁边，内置资源在 PyInstaller 的临时解包目录
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR

CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CACHE_PATH = os.path.join(APP_DIR, "cache.json")
UI_PATH = os.path.join(APP_DIR, "ui.html")              # 外置优先，方便自己改
BUNDLED_UI_PATH = os.path.join(BUNDLE_DIR, "ui.html")   # exe 内置的兜底
USAGE_PATH = os.path.join(APP_DIR, "usage.json")

SEARCH_PATH = "/api/open/v1/search/exact"

DEFAULT_CONFIG = {
    "provider": "openai",            # openai | dify
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "extra_headers": {},
    "dify_user": "qbank-bridge",
    "port": 8765,
    "access_token": "",
    "auth_mode": "strict",           # strict 校验密钥 | any 任意密钥放行
    "timeout": 12.0,
    "temperature": 0,
    "max_tokens": 512,
    "workers": 4,
    "system_prompt": "",
    "quota": 0.0,          # 总额度（元），0 表示只看用量不限额
    "price_in": 2.0,       # 输入单价，元 / 百万 token
    "price_out": 8.0,      # 输出单价，元 / 百万 token
}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def _out(text: str) -> None:
    """安全打印：pythonw 启动时 stdout 可能是 None。"""
    try:
        print(text, flush=True)
    except Exception:
        pass


def log(message: str, level: str = "info") -> None:
    global _LOG_SEQ
    with _LOG_LOCK:
        _LOG_SEQ += 1
        _LOGS.append({
            "i": _LOG_SEQ,
            "t": time.strftime("%H:%M:%S"),
            "lvl": level,
            "msg": message,
        })
        del _LOGS[:-400]
    prefix = {"info": "  ", "ok": "√ ", "warn": "! ", "err": "× "}.get(level, "  ")
    _out(time.strftime("%H:%M:%S") + " " + prefix + message)


_LOG_LOCK = threading.Lock()
_LOGS: list = []
_LOG_SEQ = 0

STATS_LOCK = threading.Lock()
STATS = {
    "total": 0,
    "ok": 0,
    "none": 0,
    "error": 0,
    "cache_hits": 0,
    "total_ms": 0,
}


def stat(name: str, inc: int = 1) -> None:
    with STATS_LOCK:
        STATS[name] = STATS.get(name, 0) + inc


# ---- token 用量 ------------------------------------------------------------ #

USAGE_LOCK = threading.Lock()


def _blank_usage() -> dict:
    return {"prompt": 0, "completion": 0, "total": 0, "calls": 0}


USAGE = {
    "date": time.strftime("%Y-%m-%d"),
    "today": _blank_usage(),
    "all": _blank_usage(),
}


def load_usage() -> None:
    """读取历史用量；跨天时把 today 归零，all 永久累计。"""
    global USAGE
    data = {}
    if os.path.exists(USAGE_PATH):
        try:
            with open(USAGE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
        except Exception:
            data = {}

    all_ = _blank_usage()
    today = _blank_usage()
    stored_all = data.get("all") if isinstance(data.get("all"), dict) else {}
    stored_today = data.get("today") if isinstance(data.get("today"), dict) else {}
    for key in all_:
        try:
            all_[key] = max(0, int(stored_all.get(key) or 0))
        except Exception:
            all_[key] = 0
    if data.get("date") == time.strftime("%Y-%m-%d"):
        for key in today:
            try:
                today[key] = max(0, int(stored_today.get(key) or 0))
            except Exception:
                today[key] = 0

    with USAGE_LOCK:
        USAGE = {"date": time.strftime("%Y-%m-%d"), "today": today, "all": all_}


def save_usage() -> None:
    with USAGE_LOCK:
        snapshot = {
            "date": USAGE["date"],
            "today": dict(USAGE["today"]),
            "all": dict(USAGE["all"]),
        }
    try:
        tmp = USAGE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, USAGE_PATH)
    except Exception:
        pass


def record_usage(prompt_tokens: int, completion_tokens: int, total_tokens: int = 0) -> None:
    """累加一次模型调用消耗。解析不到 usage 的接口传 0 也可以，只计调用次数。"""
    today_key = time.strftime("%Y-%m-%d")
    with USAGE_LOCK:
        if USAGE["date"] != today_key:
            USAGE["date"] = today_key
            USAGE["today"] = _blank_usage()
        prompt_tokens = max(0, int(prompt_tokens or 0))
        completion_tokens = max(0, int(completion_tokens or 0))
        total_tokens = max(0, int(total_tokens or 0)) or (prompt_tokens + completion_tokens)
        for bucket in (USAGE["today"], USAGE["all"]):
            bucket["prompt"] += prompt_tokens
            bucket["completion"] += completion_tokens
            bucket["total"] += total_tokens
            bucket["calls"] += 1
    save_usage()


def usage_snapshot(cfg: dict) -> dict:
    """把累计用量换算成金额与剩余额度。"""
    today_key = time.strftime("%Y-%m-%d")
    with USAGE_LOCK:
        if USAGE["date"] != today_key:      # 跨天：今日自动归零
            USAGE["date"] = today_key
            USAGE["today"] = _blank_usage()
        today = dict(USAGE["today"])
        all_ = dict(USAGE["all"])

    try:
        price_in = max(0.0, float(cfg.get("price_in") or 0))
    except Exception:
        price_in = 0.0
    try:
        price_out = max(0.0, float(cfg.get("price_out") or 0))
    except Exception:
        price_out = 0.0
    try:
        quota = max(0.0, float(cfg.get("quota") or 0))
    except Exception:
        quota = 0.0

    def cost_of(bucket: dict) -> float:
        return bucket["prompt"] / 1e6 * price_in + bucket["completion"] / 1e6 * price_out

    cost_all = cost_of(all_)
    result = {
        "today": today,
        "all": all_,
        "cost_today": round(cost_of(today), 4),
        "cost_all": round(cost_all, 4),
        "quota": quota,
        "price_in": price_in,
        "price_out": price_out,
    }
    if quota > 0:
        result["remaining"] = round(max(0.0, quota - cost_all), 4)
        result["used_pct"] = round(min(100.0, cost_all / quota * 100), 3)
    else:
        result["remaining"] = None
        result["used_pct"] = None
    return result


AUTH_LOCK = threading.Lock()
LAST_AUTH = {"t": "", "token": "", "result": "", "stem": ""}


def record_auth(result: str, token: str, stem: str = "") -> None:
    """记录最近一次来自脚本的请求，供界面展示——用来判断脚本到底连没连上。"""
    with AUTH_LOCK:
        LAST_AUTH.update({
            "t": time.strftime("%H:%M:%S"),
            "token": token,
            "result": result,
            "stem": stem,
        })


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG or k == "extra_headers"})
        except Exception as exc:  # noqa: BLE001
            _out("读取配置失败，使用默认配置：" + str(exc))
    if not cfg.get("access_token"):
        cfg["access_token"] = "llm-" + secrets.token_hex(4)
    return cfg


def save_config(cfg: dict) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


CACHE_LOCK = threading.Lock()
_CACHE: dict = {}


def load_cache() -> None:
    global _CACHE
    if not os.path.exists(CACHE_PATH):
        return
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _CACHE = data
    except Exception as exc:  # noqa: BLE001
        _out("读取缓存失败：" + str(exc))


def save_cache() -> None:
    try:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_CACHE, fh, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        _out("写入缓存失败：" + str(exc))


# --------------------------------------------------------------------------- #
# 文本处理
# --------------------------------------------------------------------------- #

TAG_RE = re.compile(r"<[^>]+>")
# 只剥离字母题号（A. / B、/ C)），与脚本自身 stripOptionLabel 的行为保持一致，
# 避免把 "1.5米" 这类数字选项误伤。
LABEL_RE = re.compile(r"^\s*[A-Za-z]\s*[.．、:：\]\)]\s*")
TYPE_LABEL_RE = re.compile(
    r"^\s*[【\[]\s*(?:(?:单选|多选|单项选择|多项选择|选择|判断|填空|简答|问答|论述|"
    r"计算|分析|阅读理解|完形填空|排序|连线|其它|其他)题|名词解释)\s*[】\]]"
)

JUDGE_TRUE = {"对", "正确", "是", "√", "t", "true", "right", "y", "yes"}
JUDGE_FALSE = {"错", "错误", "否", "×", "x", "f", "false", "wrong", "n", "no"}


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<img[^>]*src=[\"']([^\"']*)[\"'][^>]*>", r" \1 ", text, flags=re.I)
    text = TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"[ \t\r\n\u00a0]+", " ", text).strip()


def norm(text: str) -> str:
    """归一化：去题号前缀、去空白、转小写。用于选项比对。"""
    if not text:
        return ""
    value = strip_html(text)
    value = LABEL_RE.sub("", value)
    value = re.sub(r"[\s\u00a0\u3000]+", "", value)
    return value.lower()


def judge_token(value: str) -> str:
    token = norm(value)
    token = re.sub(r"[.。!！,，、:：;；\[\]()（）{}]", "", token)
    if token in JUDGE_TRUE:
        return "对"
    if token in JUDGE_FALSE:
        return "错"
    if token.endswith("正确") or token.endswith("对"):
        return "对"
    if token.endswith("错误") or token.endswith("错"):
        return "错"
    return ""


def extract_json_block(text: str):
    """从模型输出里尽力抠出一个 JSON 对象。"""
    if not text:
        return None
    body = text.strip()
    body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
    body = re.sub(r"\s*```\s*$", "", body).strip()
    try:
        return json.loads(body)
    except Exception:
        pass
    start = body.find("{")
    while start != -1:
        depth = 0
        for idx in range(start, len(body)):
            char = body[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    chunk = body[start:idx + 1]
                    try:
                        return json.loads(chunk)
                    except Exception:
                        break
        start = body.find("{", start + 1)
    return None


def as_value_list(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (int, float, bool)):
        items = [str(raw)]
    elif isinstance(raw, list):
        items = []
        for item in raw:
            if isinstance(item, (str, int, float, bool)):
                items.append(str(item))
            elif isinstance(item, dict):
                for key in ("text", "value", "answer", "content", "option"):
                    if key in item and isinstance(item[key], (str, int, float)):
                        items.append(str(item[key]))
                        break
    else:
        items = [str(raw)]
    cleaned = []
    for item in items:
        value = item.strip().strip("\"'“”").strip()
        if value:
            cleaned.append(value)
    return cleaned


def pick_option(value: str, options: list):
    """把一个答案值映射回选项下标，返回 (index, score)。"""
    if not options:
        return -1, 0.0
    raw = value.strip()
    normed = [norm(opt) for opt in options]

    # 纯字母：A / B. / （C）
    letter = re.fullmatch(r"[(\[]?\s*([A-Za-z])\s*[)）.．、:：]?", raw)
    if letter:
        idx = ord(letter.group(1).upper()) - 65
        if 0 <= idx < len(options):
            return idx, 1.0

    target = norm(raw)
    if not target:
        return -1, 0.0
    if target in normed:
        return normed.index(target), 1.0

    # 去掉选项里常见的“以上都对”冗余后再比
    for idx, opt in enumerate(normed):
        if opt and (opt.startswith(target) or target.startswith(opt)) and min(len(opt), len(target)) >= 3:
            return idx, 0.93

    best, best_idx = 0.0, -1
    for idx, opt in enumerate(normed):
        if not opt:
            continue
        ratio = difflib.SequenceMatcher(None, target, opt).ratio()
        if ratio > best:
            best, best_idx = ratio, idx
    if best >= 0.86:
        return best_idx, best
    return -1, best


def clean_option(text: str) -> str:
    """选项原文 → 去掉字母题号后的展示文本。"""
    value = strip_html(text)
    stripped = LABEL_RE.sub("", value).strip()
    return stripped or value


def resolve_values(qtype: str, options: list, raw_values: list) -> list:
    """把模型给的答案（可能是字母、可能是原文）对齐成脚本需要的选项原文。"""
    if qtype == "3":
        token = ""
        for value in raw_values:
            token = judge_token(value)
            if token:
                break
        if not token:
            return []
        # 有选项时优先返回选项原文（判断题的选项可能是「正确/错误」「√/×」），
        # 这样无论脚本按文本比对还是按对错 token 比对都能命中。
        if options:
            for opt in options:
                if judge_token(opt) == token:
                    return [clean_option(opt)]
        return [token]

    if not options:
        seen, unique = set(), []
        for value in raw_values:
            key = norm(value) or value
            if key and key not in seen:
                seen.add(key)
                unique.append(value)
        return unique

    picks = {}  # 选项下标 -> 匹配得分
    for value in raw_values:
        compact = re.sub(r"[\s,，、/|]+", "", value)

        # 先按选项原文对齐：这样 "Java" 不会被当成 J/a/v/a 四个字母拆开
        idx, score = pick_option(value, options)
        exact = idx >= 0 and score >= 0.85

        # 原文对不上，且是多选题里的 "AC" / "A,C" 这种字母简写，才逐字母解析
        if not exact and qtype == "1" and re.fullmatch(r"[A-Za-z]{2,8}", compact) and len(compact) <= len(options):
            before = len(picks)
            for char in compact:
                char_idx, char_score = pick_option(char, options)
                if char_idx >= 0 and char_score >= 0.99:
                    picks[char_idx] = max(picks.get(char_idx, 0.0), 1.0)
            if len(picks) > before:
                continue

        if exact:
            picks[idx] = max(picks.get(idx, 0.0), score)

    if not picks:
        return []

    if qtype == "1":
        return [clean_option(options[idx]) for idx in sorted(picks)]

    best_idx = max(picks.items(), key=lambda item: item[1])[0]
    return [clean_option(options[best_idx])]


def cache_key(stem: str, options: list, qtype: str) -> str:
    payload = json.dumps(
        [qtype, re.sub(r"\s+", "", strip_html(stem)), [norm(o) for o in (options or [])]],
        ensure_ascii=False,
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = (
    "你是一个严谨的答题引擎。你只输出 JSON，不输出任何解释、寒暄或 Markdown 代码块。"
)


def build_prompt(stem: str, options: list, qtype: str) -> str:
    title = TYPE_LABEL_RE.sub("", strip_html(stem))
    lines = ["题目：" + title]
    opts = [strip_html(opt) for opt in options or []]
    if opts:
        lines.append("选项：")
        for idx, opt in enumerate(opts):
            lines.append("%s. %s" % (chr(65 + idx), opt))

    if qtype == "3":
        lines.append("题型：判断题")
        lines.append('作答要求：answer 只放一个字符串，必须是 "对" 或 "错"。')
    elif qtype == "1":
        lines.append("题型：多选题（正确选项可能是 1 个，也可能是多个）")
        lines.append("作答要求：answer 里放全部正确选项的原文，逐字照抄选项文字，不要输出字母编号。")
    elif opts:
        lines.append("题型：单选题（只有一个正确选项）")
        lines.append("作答要求：answer 只放一个字符串，必须是某个选项的原文，逐字照抄，不要输出字母编号。")
    else:
        lines.append("题型：填空题 / 简答题")
        lines.append("作答要求：answer 按题目空格顺序放答案文本，答案尽量简短；只有一个空就只放一个字符串。")

    lines.append('输出格式（只输出这一行）：{"answer": ["..."], "confidence": 0.9}')
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 模型调用
# --------------------------------------------------------------------------- #

def _post_json(url: str, payload: dict, headers: dict, timeout: float):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(url, data=body, headers=headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urlerror.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise RuntimeError("API Key 无效或没有权限（HTTP %s）" % exc.code)
        if exc.code == 404:
            raise RuntimeError("接口地址不对，检查 API 地址是否要带 /v1（HTTP 404）")
        if exc.code == 429:
            raise RuntimeError("请求过于频繁被限流（HTTP 429），稍后再试或降低并发")
        if exc.code >= 500:
            raise RuntimeError("模型服务端错误（HTTP %s）：%s" % (exc.code, detail[:120]))
        raise RuntimeError("HTTP %s：%s" % (exc.code, detail or exc.reason))
    except urlerror.URLError as exc:
        raise RuntimeError("网络错误：%s" % (exc.reason,))
    except socket.timeout:
        raise RuntimeError("连接超时")
    try:
        return json.loads(raw)
    except Exception:
        raise RuntimeError("接口没有返回 JSON：" + raw[:200])


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in (None, "text"):
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def call_model(cfg: dict, prompt: str) -> str:
    provider = (cfg.get("provider") or "openai").lower()
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("还没填 API 地址")

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + str(cfg["api_key"]).strip()
    for key, value in (cfg.get("extra_headers") or {}).items():
        if key and value:
            headers[str(key)] = str(value)

    timeout = float(cfg.get("timeout") or 12)

    if provider == "dify":
        url = base + "/chat-messages"
        payload = {
            "inputs": {},
            "query": prompt,
            "response_mode": "blocking",
            "conversation_id": "",
            "user": cfg.get("dify_user") or "qbank-bridge",
        }
        data = _post_json(url, payload, headers, timeout)
        meta = data.get("metadata") or {}
        usage = meta.get("usage") or data.get("usage") or {}
        record_usage(usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"))
        answer = data.get("answer") or (data.get("data") or {}).get("answer") or ""
        if not answer:
            raise RuntimeError("Dify 返回里没有 answer 字段")
        return answer

    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload = {
        "model": cfg.get("model") or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": (cfg.get("system_prompt") or SYSTEM_PROMPT)},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(cfg.get("temperature") or 0),
        "max_tokens": int(cfg.get("max_tokens") or 512),
        "stream": False,
    }
    data = _post_json(url, payload, headers, timeout)
    usage = data.get("usage") or {}
    record_usage(usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("接口返回没有 choices：" + json.dumps(data, ensure_ascii=False)[:200])
    message = choices[0].get("message") or {}
    text = _content_to_text(message.get("content"))
    if not text.strip() and message.get("reasoning_content"):
        text = _content_to_text(message.get("reasoning_content"))
    if not text.strip():
        raise RuntimeError("模型返回内容为空")
    return text


# --------------------------------------------------------------------------- #
# 核心答题
# --------------------------------------------------------------------------- #

def answer_question(cfg: dict, stem: str, options: list, qtype: str) -> dict:
    prompt = build_prompt(stem, options, qtype)
    started = time.time()
    text = call_model(cfg, prompt)
    elapsed = int((time.time() - started) * 1000)

    parsed = extract_json_block(text)
    raw_values = []
    confidence = None
    if isinstance(parsed, dict):
        for key in ("answer", "answers", "result", "value"):
            if key in parsed:
                raw_values = as_value_list(parsed.get(key))
                break
        if isinstance(parsed.get("confidence"), (int, float)):
            confidence = float(parsed["confidence"])
    if not raw_values:
        # 模型没按格式来：退化成从纯文本里找字母 / 对错
        raw_values = as_value_list(text)

    values = resolve_values(qtype, options, raw_values)

    return {
        "values": values,
        "raw": text.strip()[:600],
        "confidence": confidence,
        "elapsed_ms": elapsed,
    }


def answer_with_cache(cfg: dict, stem: str, options: list, qtype: str):
    key = cache_key(stem, options, qtype)
    with CACHE_LOCK:
        if key in _CACHE:
            return dict(_CACHE[key]), True

    with _INFLIGHT_LOCK:
        future = _INFLIGHT.get(key)
        owner = future is None
        if owner:
            future = concurrent.futures.Future()
            _INFLIGHT[key] = future

    if not owner:
        return future.result(timeout=14), False

    try:
        result = answer_question(cfg, stem, options, qtype)
        if result["values"]:
            with CACHE_LOCK:
                _CACHE[key] = result
                save_cache()
        future.set_result(result)
        return result, False
    except BaseException as exc:  # noqa: BLE001
        future.set_exception(exc)
        raise
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(key, None)


_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict = {}


# --------------------------------------------------------------------------- #
# 题库海格式兼容：把模型答案对回原选项原文
# --------------------------------------------------------------------------- #

def _answer_norm(text: str) -> str:
    text = strip_html(str(text or ""))
    text = re.sub(r"^\s*[A-Za-z]\s*[.．、:：\)]\s*", "", text)
    return re.sub(r"[\s\u00a0\u3000，。、；：,.;:!?！？\"'“”‘’()（）\[\]【】<>《》]+", "", text).lower()


def pick_option_indexes(values, raw_options):
    """把模型给出的答案对回选项下标，尽量容忍标点、前缀、字母、轻微差异。"""
    raws = [str(item) for item in raw_options]
    norms = [_answer_norm(item) for item in raws]
    picked = []
    for value in values:
        text = strip_html(str(value)).strip()
        if not text:
            continue
        index = None
        for i, raw in enumerate(raws):
            if text == raw.strip():
                index = i
                break
        if index is None:
            target = _answer_norm(text)
            if target:
                for i, item in enumerate(norms):
                    if item and item == target:
                        index = i
                        break
        if index is None:
            letter = re.match(r"^([A-Za-z])\s*$", text) or re.match(r"^([A-Za-z])\s*[.．、:：\)]", text)
            if letter:
                i = ord(letter.group(1).upper()) - 65
                if 0 <= i < len(raws):
                    index = i
        if index is None:
            best_score, best_index = 0.0, -1
            for i, item in enumerate(norms):
                if not item:
                    continue
                score = difflib.SequenceMatcher(None, _answer_norm(text), item).ratio()
                if score > best_score:
                    best_score, best_index = score, i
            if best_index >= 0 and best_score >= 0.72:
                index = best_index
        if index is not None and index not in picked:
            picked.append(index)
    return picked


def build_th_answer(values, raw_options, qtype: str):
    """拼成题库海脚本能吃的答案：选择/多选回选项原文数组，判断回「对/错」。"""
    raws = [str(item).strip() for item in (raw_options or [])]
    indexes = pick_option_indexes(values, raws)

    if qtype == "3":
        for value in values:
            token = judge_token(str(value))
            if token:
                return token
        if indexes:
            token = judge_token(raws[indexes[0]])
            if token:
                return token
        return ""

    if indexes:
        return [raws[i] for i in indexes]

    # 选项对不上（填空题、图片题等）：原样回一个字符串，让脚本自己判断能不能用
    plain = " / ".join(str(item) for item in values if str(item).strip())
    return plain[:200]


# --------------------------------------------------------------------------- #
# 响应包
# --------------------------------------------------------------------------- #

def envelope_ok(stem: str, result: dict, cached: bool) -> dict:
    meta = {
        "billing_result": "ok",
        "source": "llm",
        "cached": bool(cached),
        "elapsed_ms": result.get("elapsed_ms"),
        "confidence": result.get("confidence"),
        "provider": "local-bridge",
        "version": APP_VERSION,
    }
    return {
        "success": True,
        "data": {
            "items": [{
                "stem": stem,
                "answer": {
                    "kind": "text",
                    "values": result["values"],
                    "raw": result.get("raw"),
                },
            }],
            "total": 1,
        },
        "error": None,
        "meta": meta,
    }


def envelope_none(stem: str, reason: str, elapsed_ms: int = 0) -> dict:
    return {
        "success": True,
        "data": {
            "items": [{
                "stem": stem,
                "answer": {"kind": "none", "values": [], "raw": reason},
            }],
            "total": 0,
        },
        "error": None,
        "meta": {"billing_result": "ok", "source": "llm", "cached": False, "elapsed_ms": elapsed_ms},
    }


def envelope_error(message: str, code: str = "UPSTREAM_ERROR", status: int = 0) -> dict:
    return {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message, "details": None},
        "meta": {"billing_result": "quota" if status == 402 else "ok", "source": "llm"},
    }


# --------------------------------------------------------------------------- #
# 脚本适配
# --------------------------------------------------------------------------- #

def patch_userscript(text: str, port: int) -> dict:
    changes = []
    local_base = "http://127.0.0.1:%d" % port

    # 1) 去掉自动更新地址，避免补丁被线上新版本覆盖回去
    new_text, count = re.subn(r"(?m)^//\s*@(?:updateURL|downloadURL)[^\n]*\r?\n?", "", text)
    if count:
        text = new_text
        changes.append("已移除自动更新地址，补丁不会被覆盖")

    # 2) 改个名字，避免和原脚本互相覆盖
    if "·本地题库" not in text:
        new_text, count = re.subn(
            r"(?m)^(//\s*@name\s+)(.+?)\s*$", r"\1\2·本地题库", text, count=1
        )
        if count:
            text = new_text
            changes.append("脚本名已改为「…·本地题库」")

    # 3) 放行本地连接
    if not re.search(r"(?m)^//\s*@connect\s+127\.0\.0\.1\s*$", text):
        new_text, count = re.subn(
            r"(?m)^//\s*@connect\s+search\.tikuhai\.com\s*$",
            "// @connect      search.tikuhai.com\n// @connect      127.0.0.1",
            text,
        )
        if count:
            text = new_text
        else:
            text = text.replace("// ==/UserScript==", "// @connect      127.0.0.1\n// ==/UserScript==", 1)
        changes.append("已放行 @connect 127.0.0.1")

    # 4) 接口地址指向本机
    if "https://search.tikuhai.com" in text:
        text = text.replace("https://search.tikuhai.com", local_base)
        changes.append("接口地址已改为 " + local_base)

    # 4b) 题库海那一套脚本（POST {key, question} 到 api1 + "/search"）
    if '"http://api.tikuhai.com"' in text:
        text = text.replace('"http://api.tikuhai.com"', '"%s"' % local_base)
        changes.append("题库海接口已改为 %s/search" % local_base)

    # 5) 放开 https 协议限制
    if 'baseUrl.protocol !== "https:"' in text:
        text = text.replace(
            'baseUrl.protocol !== "https:"',
            '["https:", "http:"].indexOf(baseUrl.protocol) === -1',
        )
        changes.append("已放开本地 HTTP 协议限制")

    return {"text": text, "changes": changes}


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #

CFG_LOCK = threading.Lock()
CONFIG = {}


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = "QbankBridge/" + APP_VERSION
        protocol_version = "HTTP/1.1"

        # -- 基础设施 -------------------------------------------------------- #
        def log_message(self, fmt, *args):  # 静音默认访问日志
            return

        def handle(self):
            # 浏览器/脚本偶尔会直接断开 keep-alive 连接，这里静默处理避免刷屏
            try:
                super().handle()
            except (ConnectionError, OSError):
                pass

        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def _json(self, status: int, payload: dict):
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _text(self, status: int, text: str, content_type="text/plain; charset=utf-8"):
            self._send(status, text.encode("utf-8"), content_type)

        def _body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _json_body(self) -> dict:
            raw = self._body()
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        # -- 路由 ------------------------------------------------------------ #
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path in ("/", "/index.html"):
                ui_file = UI_PATH if os.path.exists(UI_PATH) else BUNDLED_UI_PATH
                try:
                    with open(ui_file, "r", encoding="utf-8") as fh:
                        html = fh.read()
                except Exception as exc:  # noqa: BLE001
                    return self._text(500, "ui.html 读取失败：" + str(exc))
                return self._text(200, html, "text/html; charset=utf-8")

            if path == "/api/ping":
                return self._json(200, {"ok": True, "app": "qbank-bridge", "version": APP_VERSION})

            if path == "/api/state":
                since = 0
                try:
                    since = int((parse_qs(parsed.query).get("since") or ["0"])[0])
                except Exception:
                    since = 0
                with CFG_LOCK:
                    cfg_snapshot = dict(CONFIG)
                with STATS_LOCK:
                    stats = dict(STATS)
                with _LOG_LOCK:
                    logs = [item for item in _LOGS if item["i"] > since]
                    cursor = _LOGS[-1]["i"] if _LOGS else since
                with CACHE_LOCK:
                    cache_size = len(_CACHE)
                with AUTH_LOCK:
                    last_auth = dict(LAST_AUTH)
                return self._json(200, {
                    "ok": True,
                    "version": APP_VERSION,
                    "config": cfg_snapshot,
                    "stats": stats,
                    "usage": usage_snapshot(cfg_snapshot),
                    "cache_size": cache_size,
                    "last_auth": last_auth,
                    "logs": logs,
                    "cursor": max(cursor, since),
                    "endpoint": "http://127.0.0.1:%d" % int(cfg_snapshot.get("port") or 8765),
                })

            return self._json(404, {"ok": False, "message": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == SEARCH_PATH:
                return self.handle_search()
            if path == "/search":
                return self.handle_th_search()
            if path == "/api/config":
                return self.handle_config()
            if path == "/api/test":
                return self.handle_test()
            if path == "/api/patch":
                return self.handle_patch()
            if path == "/api/usage/reset":
                scope = str(self._json_body().get("scope") or "all").lower()
                with USAGE_LOCK:
                    if scope == "today":
                        USAGE["today"] = _blank_usage()
                    else:
                        USAGE["today"] = _blank_usage()
                        USAGE["all"] = _blank_usage()
                        scope = "all"
                save_usage()
                log("已重置用量统计（%s）" % ("今日" if scope == "today" else "全部"), "warn")
                with CFG_LOCK:
                    snap = dict(CONFIG)
                return self._json(200, {"ok": True, "usage": usage_snapshot(snap)})
            if path == "/api/cache/clear":
                with CACHE_LOCK:
                    _CACHE.clear()
                    save_cache()
                log("已清空答案缓存", "warn")
                return self._json(200, {"ok": True})
            self._body()  # 丢弃未知路由的请求体，避免 keep-alive 串包
            return self._json(404, {"ok": False, "message": "not found"})

        # -- 答题接口（脚本调用） -------------------------------------------- #
        def handle_search(self):
            with CFG_LOCK:
                cfg = dict(CONFIG)

            token = (self.headers.get("X-Consumer-Token") or "").strip()
            payload = self._json_body()
            stem = str(payload.get("stem") or "").strip()
            raw_options = payload.get("options") or []
            qtype = str(payload.get("type") or "").strip() or "0"
            options = [strip_html(str(item)) for item in raw_options if str(item).strip()] if isinstance(raw_options, list) else []
            preview = re.sub(r"\s+", " ", strip_html(stem))[:36]
            stat("total")

            # 鉴权：strict 才校验；any 模式任何密钥都放行
            expected = (cfg.get("access_token") or "").strip()
            auth_mode = (cfg.get("auth_mode") or "strict").lower()
            if auth_mode == "strict" and expected:
                if not token:
                    record_auth("none", "", preview)
                    log("收到请求，但脚本没带密钥：%s" % preview, "warn")
                    return self._json(200, envelope_error(
                        "脚本没有提交密钥：请在脚本「答题」页填入 %s 后刷新页面" % expected,
                        "AUTH_MISSING"))
                if not hmac.compare_digest(token, expected):
                    record_auth("mismatch", token, preview)
                    log("密钥不匹配：脚本带来的是「%s」，本地是「%s」" % (token, expected), "err")
                    return self._json(200, envelope_error(
                        "密钥不匹配：脚本带来的是「%s」，本地窗口里的是「%s」，复制粘贴时漏了或多了字符"
                        % (token, expected), "AUTH_MISMATCH"))
            record_auth("ok", token, preview)

            if not stem:
                return self._json(200, envelope_error("题干为空", "CLIENT_ERROR"))

            if not options and qtype != "3" and not re.search(r"[\u4e00-\u9fff]", strip_html(stem)):
                return self._json(200, envelope_error("题干无效", "CLIENT_ERROR"))

            started = time.time()

            try:
                result, cached = answer_with_cache(cfg, stem, options, qtype)
            except concurrent.futures.TimeoutError:
                stat("error")
                log("超时：%s" % preview, "err")
                return self._json(200, envelope_error("本地桥等待超时，请重试"))
            except Exception as exc:  # noqa: BLE001
                stat("error")
                message = str(exc) or "模型调用失败"
                log("失败：%s → %s" % (preview, message), "err")
                return self._json(200, envelope_error(message))

            elapsed = int((time.time() - started) * 1000)
            if cached:
                stat("cache_hits")
            if result["values"]:
                stat("ok")
                stat("total_ms", result.get("elapsed_ms") or elapsed)
                log("%s：%s → %s（%dms）" % (
                    "缓存命中" if cached else "模型作答",
                    preview,
                    " / ".join(result["values"])[:40],
                    elapsed,
                ), "ok")
                return self._json(200, envelope_ok(stem, result, cached))

            stat("none")
            log("无答案：%s → %s" % (preview, (result.get("raw") or "")[:60]), "warn")
            return self._json(200, envelope_none(stem, result.get("raw") or "模型未给出可用答案", elapsed))

        # -- 题库海同形接口（老式第三方题库脚本直连本机） --------------------- #
        def handle_th_search(self):
            """POST /search：收 {key, question, options, type}，回 {code, data:{answer}}"""
            with CFG_LOCK:
                cfg = dict(CONFIG)

            payload = self._json_body()
            token = str(payload.get("key") or self.headers.get("X-Consumer-Token") or "").strip()
            stem = strip_html(str(payload.get("question") or "")).strip()
            raw_options = payload.get("options")
            raw_options = [str(item) for item in raw_options] if isinstance(raw_options, list) else []
            qtype = str(payload.get("type") or "0").strip() or "0"
            preview = re.sub(r"\s+", " ", stem)[:36]
            stat("total")

            expected = (cfg.get("access_token") or "").strip()
            auth_mode = (cfg.get("auth_mode") or "strict").lower()
            if auth_mode == "strict" and expected:
                if not token:
                    record_auth("none", "", preview)
                    log("收到 /search 请求，但没带密钥：%s" % preview, "warn")
                    return self._json(200, {"code": -1, "data": {},
                                            "msg": "没填密钥：把本地窗口里的 %s 填进脚本的题库密钥" % expected})
                if not hmac.compare_digest(token, expected):
                    record_auth("mismatch", token, preview)
                    log("密钥不匹配：脚本带来的是「%s」，本地是「%s」" % (token, expected), "err")
                    return self._json(200, {"code": -1, "data": {},
                                            "msg": "密钥不匹配：脚本里是「%s」，本地是「%s」" % (token, expected)})
            record_auth("ok", token, preview)

            if not stem:
                return self._json(200, {"code": -1, "data": {}, "msg": "题干为空"})

            options = [strip_html(item) for item in raw_options if item.strip()]

            started = time.time()
            try:
                result, cached = answer_with_cache(cfg, stem, options, qtype)
            except concurrent.futures.TimeoutError:
                stat("error")
                log("超时：%s" % preview, "err")
                return self._json(200, {"code": -1, "data": {}, "msg": "本地桥等待超时，请重试"})
            except Exception as exc:  # noqa: BLE001
                stat("error")
                message = str(exc) or "模型调用失败"
                log("失败：%s → %s" % (preview, message), "err")
                return self._json(200, {"code": -1, "data": {}, "msg": message})

            elapsed = int((time.time() - started) * 1000)
            if cached:
                stat("cache_hits")
            answer = build_th_answer(result["values"], raw_options, qtype)
            if answer:
                stat("ok")
                stat("total_ms", result.get("elapsed_ms") or elapsed)
                log("%s：%s → %s（%dms）" % (
                    "缓存命中" if cached else "模型作答",
                    preview,
                    str(answer)[:40],
                    elapsed,
                ), "ok")
            else:
                stat("none")
                log("无答案：%s → %s" % (preview, (result.get("raw") or "")[:60]), "warn")
            return self._json(200, {"code": 1, "msg": "ok", "data": {
                "answer": answer, "num": "", "usenum": ""}})

        # -- 配置 ------------------------------------------------------------ #
        def handle_config(self):
            data = self._json_body()
            incoming = data.get("config") if isinstance(data.get("config"), dict) else data
            with CFG_LOCK:
                old = dict(CONFIG)
                new = dict(CONFIG)

            for key in DEFAULT_CONFIG:
                if key in incoming:
                    new[key] = incoming[key]

            try:
                new["port"] = max(1024, min(65535, int(new.get("port") or 8765)))
            except Exception:
                new["port"] = 8765
            try:
                new["timeout"] = max(3.0, min(14.0, float(new.get("timeout") or 12)))
            except Exception:
                new["timeout"] = 12.0
            try:
                new["workers"] = max(1, min(12, int(new.get("workers") or 4)))
            except Exception:
                new["workers"] = 4
            for money_key in ("quota", "price_in", "price_out"):
                try:
                    new[money_key] = max(0.0, float(new.get(money_key) or 0))
                except Exception:
                    new[money_key] = 0.0
            new["access_token"] = str(new.get("access_token") or "").strip() or ("llm-" + secrets.token_hex(4))
            new["auth_mode"] = "any" if str(new.get("auth_mode") or "").lower() == "any" else "strict"
            if not isinstance(new.get("extra_headers"), dict):
                new["extra_headers"] = {}

            with CFG_LOCK:
                CONFIG.update(new)
            save_config(new)

            restarted = old.get("port") != new.get("port") or old.get("workers") != new.get("workers")
            if restarted:
                log("配置已保存；端口/并发改动需重启程序后生效", "warn")
            else:
                log("配置已保存", "ok")
            return self._json(200, {"ok": True, "config": new, "restart_needed": restarted})

        # -- 自测 ------------------------------------------------------------ #
        def handle_test(self):
            data = self._json_body()
            stem = str(data.get("stem") or "中国的首都是哪座城市？")
            options = data.get("options")
            if not isinstance(options, list) or not options:
                options = ["北京", "上海", "广州", "深圳"]
            options = [str(item) for item in options]
            qtype = str(data.get("type") or "0")

            with CFG_LOCK:
                cfg = dict(CONFIG)
            started = time.time()
            try:
                result = answer_question(cfg, stem, options, qtype)
            except Exception as exc:  # noqa: BLE001
                log("自测失败：" + str(exc), "err")
                return self._json(200, {"ok": False, "message": str(exc)})
            elapsed = int((time.time() - started) * 1000)
            log("自测通过：模型返回「%s」（%dms）" % (" / ".join(result["values"]) or "空", elapsed), "ok")
            return self._json(200, {
                "ok": True,
                "values": result["values"],
                "raw": result.get("raw"),
                "elapsed_ms": elapsed,
                "confidence": result.get("confidence"),
            })

        # -- 脚本适配 -------------------------------------------------------- #
        def handle_patch(self):
            data = self._json_body()
            text = data.get("text")
            if not isinstance(text, str) or not text.strip():
                return self._json(200, {"ok": False, "message": "没有收到脚本内容"})
            with CFG_LOCK:
                port = int(CONFIG.get("port") or 8765)
            result = patch_userscript(text, port)
            if not result["changes"]:
                return self._json(200, {
                    "ok": False,
                    "message": "没找到需要修改的位置：这个脚本可能改过结构，请按「手动改 3 处」处理",
                })

            # 同时落盘一份，浏览器下载被拦时也能直接拿到文件
            filename = "网课小助手-本地题库.user.js"
            saved_path = ""
            try:
                out_dir = os.path.join(APP_DIR, "local-scripts")
                os.makedirs(out_dir, exist_ok=True)
                saved_path = os.path.join(out_dir, filename)
                with open(saved_path, "w", encoding="utf-8") as fh:
                    fh.write(result["text"])
            except Exception as exc:  # noqa: BLE001
                saved_path = ""
                log("脚本落盘失败：" + str(exc), "warn")

            log("已生成适配脚本：" + "；".join(result["changes"]), "ok")
            if saved_path:
                log("适配脚本已保存到 " + saved_path)
            return self._json(200, {
                "ok": True,
                "text": result["text"],
                "changes": result["changes"],
                "filename": filename,
                "saved_path": saved_path,
            })

    return Handler


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #

def is_our_service(port: int) -> bool:
    try:
        with urlrequest.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=0.6) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data.get("app") == "qbank-bridge"
    except Exception:
        return False


def find_running_port(base_port: int) -> int:
    """找本程序已经在跑的端口（可能因端口被占用而换过端口）。找不到返回 0。"""
    for offset in range(0, 21):
        port = base_port + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                if sock.connect_ex(("127.0.0.1", port)) != 0:
                    continue
        except OSError:
            continue
        if is_our_service(port):
            return port
    return 0


def bind_server(preferred_port: int):
    for offset in range(0, 21):
        port = preferred_port + offset
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), make_handler())
            server.daemon_threads = True
            return server, port
        except OSError as exc:
            if getattr(exc, "errno", None) in (48, 98, 10048):
                continue
            raise
    raise RuntimeError("端口 %d 起连续 20 个端口都被占用" % preferred_port)


def main() -> None:
    global CONFIG

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    load_cache()
    load_usage()
    cfg = load_config()
    CONFIG = cfg
    save_config(cfg)

    base_port = int(cfg.get("port") or 8765)

    running_port = find_running_port(base_port)
    if running_port:
        running_url = "http://127.0.0.1:%d" % running_port
        log("检测到服务已在运行：%s" % running_url, "ok")
        _out("  服务已在运行，请在浏览器手动打开：%s" % running_url)
        # 退出码 3：告诉启动脚本「本来就在跑」，不要当成启动失败
        raise SystemExit(3)

    server, port = bind_server(base_port)
    with CFG_LOCK:
        CONFIG["port"] = port
    if port != base_port:
        save_config(CONFIG)
        log("端口 %d 被占用，已自动切换到 %d" % (base_port, port), "warn")

    url = "http://127.0.0.1:%d" % port

    _out("")
    _out("  LLM 题库桥 v%s 已启动" % APP_VERSION)
    _out("  服务地址：%s" % url)
    _out("  接口地址：%s%s" % (url, SEARCH_PATH))
    _out("  访问密钥：%s" % CONFIG.get("access_token"))
    _out("  关闭这个黑窗口即可停止服务")
    _out("")

    log("服务已启动，监听 127.0.0.1:%d" % port, "ok")
    log("题库接口 %s%s" % (url, SEARCH_PATH))
    log("当前模型：%s（%s）" % (CONFIG.get("model") or "-", CONFIG.get("base_url") or "-"))
    if not CONFIG.get("api_key"):
        log("还没填写模型 API Key，请在窗口里补上", "warn")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        while True:
            time.sleep(0.8)
    except KeyboardInterrupt:
        log("收到退出信号，正在停止…", "warn")
    finally:
        try:
            server.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
