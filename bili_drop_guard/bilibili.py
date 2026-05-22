from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Dict, List
from uuid import uuid4

import requests


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


@dataclass
class LoginInfo:
    logged_in: bool
    uname: str = ""
    mid: int = 0
    message: str = ""


@dataclass
class RoomInfo:
    room_id: int
    title: str = ""
    live_status: int = 0
    online: int = 0
    anchor: str = ""
    anchor_uid: int = 0
    parent_area_id: int = 0
    area_id: int = 0
    message: str = ""


def parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    return {key: morsel.value for key, morsel in cookie.items()}


def normalize_room_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text

    match = re.search(r"(?:^|[/:])live\.bilibili\.com/(?:blanc/)?(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(r"live\.bilibili\.com/(?:blanc/)?(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    return ""


def _json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def calc_heartbeat_sign(data: Dict[str, Any], secret_rule: List[int]) -> str:
    parent_id, area_id, seq_id, room_id = json.loads(data["id"])
    buvid, uuid = json.loads(data["device"])
    payload = {
        "platform": "web",
        "parent_id": parent_id,
        "area_id": area_id,
        "seq_id": seq_id,
        "room_id": room_id,
        "buvid": buvid,
        "uuid": uuid,
        "ets": data["ets"],
        "time": data["time"],
        "ts": data["ts"],
    }
    digest = _json_compact(payload)
    algorithms = {
        0: hashlib.md5,
        1: hashlib.sha1,
        2: hashlib.sha256,
        3: hashlib.sha224,
        4: hashlib.sha512,
        5: hashlib.sha384,
    }
    key = str(data["benchmark"]).encode("utf-8")
    for rule in secret_rule:
        algorithm = algorithms.get(int(rule))
        if algorithm:
            digest = hmac.new(key, digest.encode("utf-8"), algorithm).hexdigest()
    return digest


class BilibiliClient:
    def __init__(self, cookie_header: str) -> None:
        self.cookie_header = cookie_header
        self.cookies = parse_cookie_header(cookie_header)
        self._buvid = self.cookies.get("buvid3") or self.cookies.get("buvid4") or str(uuid4())
        source = self.cookies.get("DedeUserID") or self._buvid
        self._device_uuid = str(uuid4()).replace("-", "")[:8] + str(abs(hash(source)))[:8]
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": "https://live.bilibili.com/",
                "Origin": "https://live.bilibili.com",
            }
        )
        for key, value in self.cookies.items():
            self.session.cookies.set(key, value, domain=".bilibili.com")

    @property
    def csrf(self) -> str:
        return self.cookies.get("bili_jct", "")

    @property
    def buvid(self) -> str:
        return self._buvid

    @property
    def device_uuid(self) -> str:
        return self._device_uuid

    def check_login(self) -> LoginInfo:
        try:
            response = self.session.get(
                "https://api.bilibili.com/x/web-interface/nav",
                timeout=12,
            )
            data = _decode_json_response(response)
        except Exception as exc:
            return LoginInfo(False, message=f"登录状态检查失败：{exc}")

        if data.get("code") != 0:
            return LoginInfo(False, message=str(data.get("message", "接口返回异常")))

        payload = data.get("data") or {}
        if not payload.get("isLogin"):
            return LoginInfo(False, message="Cookie 未登录或已过期")
        return LoginInfo(
            True,
            uname=str(payload.get("uname") or ""),
            mid=int(payload.get("mid") or 0),
            message="已登录",
        )

    def get_room_info(self, room_id: str) -> RoomInfo:
        normalized_room_id = normalize_room_id(room_id)
        if not normalized_room_id:
            return RoomInfo(room_id=0, message="直播间号格式不正确，请填写纯数字房间号或 live.bilibili.com 链接")
        try:
            response = self.session.get(
                "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
                params={"room_id": normalized_room_id},
                timeout=12,
            )
            data = _decode_json_response(response)
        except Exception as exc:
            return RoomInfo(room_id=0, message=f"房间状态检查失败：{exc}")

        if data.get("code") != 0:
            return RoomInfo(room_id=0, message=str(data.get("message", "接口返回异常")))

        payload = data.get("data") or {}
        room = payload.get("room_info") or payload
        anchor = (payload.get("anchor_info") or {}).get("base_info") or {}
        return RoomInfo(
            room_id=int(room.get("room_id") or 0),
            title=str(room.get("title") or ""),
            live_status=int(room.get("live_status") or 0),
            online=int(room.get("online") or 0),
            anchor=str(anchor.get("uname") or ""),
            anchor_uid=int(anchor.get("uid") or room.get("uid") or 0),
            parent_area_id=int(room.get("parent_area_id") or 0),
            area_id=int(room.get("area_id") or 0),
            message="直播中" if int(room.get("live_status") or 0) == 1 else "未开播",
        )

    def enter_room_heartbeat(self, room: RoomInfo) -> Dict[str, Any]:
        data = {
            "id": _json_compact([room.parent_area_id, room.area_id, 0, room.room_id]),
            "device": _json_compact([self.buvid, self.device_uuid]),
            "ts": int(time.time() * 1000),
            "is_patch": 0,
            "heart_beat": "[]",
            "ua": USER_AGENT,
            "csrf_token": self.csrf,
            "csrf": self.csrf,
            "visit_id": "",
        }
        return self._post_form(
            "https://live-trace.bilibili.com/xlive/data-interface/v1/x25Kn/E",
            room.room_id,
            data,
        )

    def in_room_heartbeat(
        self,
        room: RoomInfo,
        sequence: int,
        interval: int,
        ets: int,
        secret_key: str,
        secret_rule: List[int],
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": _json_compact([room.parent_area_id, room.area_id, sequence, room.room_id]),
            "device": _json_compact([self.buvid, self.device_uuid]),
            "ets": ets,
            "benchmark": secret_key,
            "time": interval,
            "ts": int(time.time() * 1000),
            "ua": USER_AGENT,
        }
        data.update(
            {
                "csrf_token": self.csrf,
                "csrf": self.csrf,
                "visit_id": "",
                "s": calc_heartbeat_sign(data, secret_rule),
            }
        )
        return self._post_form(
            "https://live-trace.bilibili.com/xlive/data-interface/v1/x25Kn/X",
            room.room_id,
            data,
        )

    def get_user_task_progress(self, up_id: int, task_id: str | None = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"target_id": up_id}
        if task_id:
            params["task_id"] = task_id
        return self._get_data(
            "https://api.live.bilibili.com/xlive/app-ucenter/v1/userTask/GetUserTaskProgress",
            params=params,
        )

    def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> Dict[str, Any]:
        url = "https://api.live.bilibili.com/xlive/app-ucenter/v1/userTask/UserTaskReceiveRewards"
        payloads = [
            {"target_id": up_id, "csrf": self.csrf, "csrf_token": self.csrf},
            {"up_id": up_id, "csrf": self.csrf, "csrf_token": self.csrf},
            {"uid": up_id, "csrf": self.csrf, "csrf_token": self.csrf},
        ]
        if task_id:
            payloads = [
                {"target_id": up_id, "task_id": task_id, "csrf": self.csrf, "csrf_token": self.csrf},
                {"up_id": up_id, "task_id": task_id, "csrf": self.csrf, "csrf_token": self.csrf},
                {"uid": up_id, "task_id": task_id, "csrf": self.csrf, "csrf_token": self.csrf},
                {"task_id": task_id, "csrf": self.csrf, "csrf_token": self.csrf},
            ] + payloads
        errors: list[str] = []
        for payload in payloads:
            try:
                return self._post_form(url, room_id=0, data=payload)
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("；".join(errors))

    def _get_data(self, url: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        response = self.session.get(url, params=params, timeout=12)
        payload = _decode_json_response(response)
        if payload.get("code") != 0:
            raise RuntimeError(str(payload.get("message", "接口返回异常")))
        data = payload.get("data")
        return data if isinstance(data, dict) else {"value": data}

    def _post_form(self, url: str, room_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Referer": f"https://live.bilibili.com/{room_id}" if room_id else "https://live.bilibili.com/",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        response = self.session.post(url, headers=headers, data=data, timeout=12)
        payload = _decode_json_response(response)
        if payload.get("code") != 0:
            raise RuntimeError(str(payload.get("message", "接口返回异常")))
        result = payload.get("data")
        return result if isinstance(result, dict) else {"value": result}


def _decode_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = response.status_code
        preview = (response.text or "").strip().replace("\n", " ")[:120]
        raise RuntimeError(f"HTTP {status}：{preview or exc}") from exc

    content_type = response.headers.get("Content-Type", "")
    if content_type and "json" not in content_type.lower():
        preview = (response.text or "").strip().replace("\n", " ")[:120]
        raise RuntimeError(f"接口未返回 JSON，可能登录失效或触发风控：{preview}")

    try:
        payload = response.json()
    except ValueError as exc:
        preview = (response.text or "").strip().replace("\n", " ")[:120]
        raise RuntimeError(f"接口 JSON 解析失败：{preview}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("接口返回格式异常：JSON 根节点不是对象")
    return payload
