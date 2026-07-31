from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import base64
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from typing import Any, Dict, List
from uuid import uuid4

import json5
import requests


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

DEVICE_COOKIE_NAMES = {
    "buvid3",
    "buvid4",
    "buvid_fp",
    "buvid_fp_plain",
    "LIVE_BUVID",
    "_uuid",
    "b_lsid",
    "b_nut",
}
BILIBILI_TIMEZONE = timezone(timedelta(hours=8))

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32,
    15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19,
    29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52,
]


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


def make_session_buvid() -> str:
    """生成一个全新的 live_buvid，模仿 B 站 buvid3 的 UUID+infoc 格式。

    后台心跳里每个 worker 都用自己的 live_buvid，B 站才会把它们当作不同设备
    分别累计观看时长。若所有 worker 共享 Cookie 里的 buvid3，B 站会把它们
    去重成一个会话，结果 N 路心跳只算一路。
    """

    return str(uuid4()).upper() + "infoc"


def make_session_device_uuid() -> str:
    return uuid4().hex


def make_session_cookie_overrides(session_buvid: str, session_device_uuid: str) -> Dict[str, str]:
    """Build an isolated device cookie set for one background watch route."""

    now = int(time.time())
    fingerprint = hashlib.md5(f"{session_buvid}:{session_device_uuid}:{now}".encode("utf-8")).hexdigest()
    return {
        "buvid3": session_buvid,
        "buvid4": f"{session_buvid}-{now}",
        "buvid_fp": fingerprint,
        "buvid_fp_plain": fingerprint,
        "LIVE_BUVID": f"AUTO{uuid4().int % 10**16:016d}",
        "_uuid": str(uuid4()).upper() + "infoc",
        "b_lsid": f"{uuid4().hex[:8].upper()}_{format(now, 'X')}",
        "b_nut": str(now),
    }


class BilibiliClient:
    def __init__(
        self,
        cookie_header: str,
        *,
        session_buvid: str | None = None,
        session_device_uuid: str | None = None,
    ) -> None:
        self.cookie_header = cookie_header
        self.cookies = parse_cookie_header(cookie_header)
        if session_buvid:
            self._buvid = session_buvid
        else:
            self._buvid = self.cookies.get("buvid3") or self.cookies.get("buvid4") or str(uuid4())
        if session_device_uuid:
            self._device_uuid = session_device_uuid
        else:
            source = self.cookies.get("DedeUserID") or self._buvid
            self._device_uuid = str(uuid4()).replace("-", "")[:8] + str(abs(hash(source)))[:8]
        self._visit_id = uuid4().hex[:16]
        self.session = requests.Session()
        self._wbi_keys: tuple[str, str] | None = None
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": "https://live.bilibili.com/",
                "Origin": "https://live.bilibili.com",
            }
        )
        for key, value in self.cookies.items():
            self.session.cookies.set(key, value, domain=".bilibili.com")
        if session_buvid:
            # 覆盖整组设备 cookie，而不是只改 buvid3，让每路后台计时保持独立会话身份；
            # 若 buvid4、buvid_fp、LIVE_BUVID 等仍复用原值，活动侧可能把多路合并。
            for key, value in make_session_cookie_overrides(self._buvid, self._device_uuid).items():
                self.session.cookies.set(key, value, domain=".bilibili.com")

    def close(self) -> None:
        """释放当前计时会话持有的连接池和套接字。"""
        self.session.close()

    @property
    def csrf(self) -> str:
        return self.cookies.get("bili_jct", "")

    @property
    def buvid(self) -> str:
        return self._buvid

    @property
    def device_uuid(self) -> str:
        return self._device_uuid

    @property
    def visit_id(self) -> str:
        return self._visit_id

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

    def room_entry_action(self, room: RoomInfo) -> Dict[str, Any]:
        """注册"进入直播间"动作，让后续心跳能计入当前观看会话。"""

        if not self.csrf:
            raise RuntimeError("Cookie 缺少 bili_jct，无法上报进入直播间动作")
        data = {
            "room_id": room.room_id,
            "platform": "pc",
            "csrf_token": self.csrf,
            "csrf": self.csrf,
            "visit_id": self.visit_id,
        }
        return self._post_form(
            "https://api.live.bilibili.com/xlive/web-room/v1/index/roomEntryAction",
            room.room_id,
            data,
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
            "visit_id": self.visit_id,
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
                "visit_id": self.visit_id,
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

    def discover_live_activity_tasks(self, room_id: str) -> Dict[str, Any]:
        normalized_room_id = normalize_room_id(room_id)
        if not normalized_room_id:
            raise RuntimeError("直播间号格式不正确，无法自动获取任务 ID")

        response = self.session.get(f"https://live.bilibili.com/{normalized_room_id}", timeout=15)
        response.raise_for_status()
        states = _extract_live_activity_states(response.text)
        if not states:
            raise RuntimeError("直播页没有找到活动任务配置")

        tasks: list[dict[str, Any]] = []
        tracking_task_ids: list[str] = []
        seen: set[str] = set()
        seen_tracking: set[str] = set()
        group_records: list[dict[str, Any]] = []
        for state in states:
            tab_labels = _extract_tab_labels(state)
            for group_index, group in enumerate(_extract_era_task_groups(state)):
                group_label = _task_group_label(group, tab_labels, group_index)
                if group_label and not any(record["label"] == group_label for record in group_records):
                    group_records.append({"label": group_label, "index": len(group_records)})
                for task in group.get("tasklist") or []:
                    if not isinstance(task, dict):
                        continue
                    parent_task_id = str(task.get("taskId") or task.get("task_id") or "").strip()
                    if not parent_task_id:
                        continue
                    if parent_task_id not in seen_tracking:
                        seen_tracking.add(parent_task_id)
                        tracking_task_ids.append(parent_task_id)
                    task_group_label = _task_date_label(task) or group_label
                    checkpoint_tasks = _activity_checkpoint_tasks(
                        task,
                        parent_task_id=parent_task_id,
                        group_label=task_group_label,
                        group_index=group_index,
                    )
                    if checkpoint_tasks:
                        for checkpoint_task in checkpoint_tasks:
                            if group.get("active_start") is not None:
                                checkpoint_task["active_start"] = group["active_start"]
                            if group.get("active_end") is not None:
                                checkpoint_task["active_end"] = group["active_end"]
                            task_id = checkpoint_task["task_id"]
                            if task_id in seen:
                                continue
                            seen.add(task_id)
                            tasks.append(checkpoint_task)
                    elif parent_task_id not in seen:
                        seen.add(parent_task_id)
                        page_task = _activity_task_from_page_task(
                            task,
                            parent_task_id,
                            task_group_label,
                            group_index,
                        )
                        if group.get("active_start") is not None:
                            page_task["active_start"] = group["active_start"]
                        if group.get("active_end") is not None:
                            page_task["active_end"] = group["active_end"]
                        tasks.append(page_task)

        if not tasks:
            raise RuntimeError("直播页没有找到活动任务 ID，请确认直播间页面有本次掉宝任务")
        result = {
            "tasks": tasks,
            "tracking_task_ids": tracking_task_ids,
            "groups": group_records,
        }
        server_time = _response_server_timestamp(response)
        if server_time is not None:
            result["server_time"] = server_time
        return result

    def get_activity_task_progress(self, task_ids: list[str]) -> Dict[str, Any]:
        normalized_task_ids = [str(task_id).strip() for task_id in task_ids if str(task_id).strip()]
        if not normalized_task_ids:
            return {"list": []}
        progress = self._get_data(
            "https://api.bilibili.com/x/task/totalv2",
            params={
                "task_ids": ",".join(normalized_task_ids),
                "need_all_invited_info": "false",
            },
        )
        return _normalize_activity_task_progress(progress, normalized_task_ids)

    def get_activity_mission_info(self, task_id: str) -> Dict[str, Any]:
        params = self._wbi_signed_params({"task_id": task_id})
        return self._get_data("https://api.bilibili.com/x/activity_components/mission/info", params=params)

    def get_activity_mission_progress(self, task_ids: list[str]) -> Dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        for task_id in [str(item).strip() for item in task_ids if str(item).strip()]:
            info = dict(self.get_activity_mission_info(task_id))
            info.setdefault("task_id", task_id)
            reward = info.get("reward_info") if isinstance(info.get("reward_info"), dict) else {}
            if reward.get("award_name") and not info.get("award_name"):
                info["award_name"] = reward.get("award_name")
            tasks.append(info)
        return {"tasks": tasks}

    def claim_activity_mission_reward(self, task_id: str) -> Dict[str, Any]:
        if not self.csrf:
            raise RuntimeError("Cookie 缺少 bili_jct，无法提交领奖请求")
        info = self.get_activity_mission_info(task_id)
        reward = info.get("reward_info") if isinstance(info.get("reward_info"), dict) else {}
        payload = {
            "task_id": task_id,
            "activity_id": info.get("act_id") or "",
            "activity_name": info.get("act_name") or "",
            "task_name": info.get("task_name") or "",
            "reward_name": reward.get("award_name") or "",
            "gaia_vtoken": info.get("gaia_vtoken") or info.get("vtoken") or "",
            "receive_from": "missionPage",
            "csrf": self.csrf,
            "csrf_token": self.csrf,
        }
        params = self._wbi_signed_params({})
        return self._post_form(
            "https://api.bilibili.com/x/activity_components/mission/receive",
            room_id=0,
            data=payload,
            params=params,
        )

    def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> Dict[str, Any]:
        if not self.csrf:
            raise RuntimeError("Cookie 缺少 bili_jct，无法提交领奖请求")
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
                if _is_terminal_claim_error(exc):
                    raise
                errors.append(str(exc))
        raise RuntimeError("；".join(errors))

    def web_live_heartbeat(self, room_id: int, interval: int = 60) -> Dict[str, Any]:
        heartbeat = base64.b64encode(f"{max(10, int(interval or 60))}|{int(room_id)}|1|0".encode("utf-8")).decode("ascii")
        return self._get_data(
            "https://live-trace.bilibili.com/xlive/rdata-interface/v1/heartbeat/webHeartBeat",
            params={"hb": heartbeat, "pf": "web"},
        )

    def _get_data(self, url: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        response = self.session.get(url, params=params, timeout=12)
        payload = _decode_json_response(response)
        if payload.get("code") != 0:
            raise RuntimeError(str(payload.get("message", "接口返回异常")))
        data = payload.get("data")
        return data if isinstance(data, dict) else {"value": data}

    def _post_form(self, url: str, room_id: int, data: Dict[str, Any], params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        headers = {
            "Referer": f"https://live.bilibili.com/{room_id}" if room_id else "https://live.bilibili.com/",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        response = self.session.post(url, headers=headers, params=params, data=data, timeout=12)
        payload = _decode_json_response(response)
        if payload.get("code") != 0:
            raise RuntimeError(str(payload.get("message", "接口返回异常")))
        result = payload.get("data")
        return result if isinstance(result, dict) else {"value": result}

    def _wbi_signed_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        img_key, sub_key = self._get_wbi_keys()
        raw_key = img_key + sub_key
        mixin_key = "".join(raw_key[index] for index in MIXIN_KEY_ENC_TAB if index < len(raw_key))[:32]
        signed = {key: "" if value is None else str(value) for key, value in params.items()}
        signed["wts"] = str(int(time.time()))
        signed = dict(sorted(signed.items()))
        filtered = {
            key: "".join(char for char in value if char not in "!'()*")
            for key, value in signed.items()
        }
        query = urllib.parse.urlencode(filtered)
        filtered["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return filtered

    def _get_wbi_keys(self) -> tuple[str, str]:
        if self._wbi_keys:
            return self._wbi_keys
        response = self.session.get("https://api.bilibili.com/x/web-interface/nav", timeout=12)
        payload = _decode_json_response(response)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        wbi_img = data.get("wbi_img") if isinstance(data.get("wbi_img"), dict) else {}
        img_url = str(wbi_img.get("img_url") or "")
        sub_url = str(wbi_img.get("sub_url") or "")
        img_key = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
        sub_key = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
        if not img_key or not sub_key:
            raise RuntimeError("获取 WBI 签名密钥失败")
        self._wbi_keys = (img_key, sub_key)
        return self._wbi_keys


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


def _response_server_timestamp(response: requests.Response) -> float | None:
    date_header = str(response.headers.get("Date") or "").strip()
    if not date_header:
        return None
    try:
        server_time = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, OverflowError):
        return None
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=timezone.utc)
    return server_time.timestamp()


def _extract_live_activity_states(html: str) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    seen_raw: set[str] = set()
    for marker in ("window.__BILIACT_EVAPAGEDATA__", "window.__initialState"):
        for raw in _extract_json_assignments(html, marker):
            if raw in seen_raw:
                continue
            seen_raw.add(raw)
            try:
                value = json.loads(raw)
            except ValueError:
                try:
                    value = json5.loads(_normalize_js_boolean_shorthand(raw))
                except ValueError:
                    continue
            if isinstance(value, dict):
                states.append(value)
    return states


def _normalize_js_boolean_shorthand(text: str) -> str:
    """Convert Bilibili's ``!0``/``!1`` booleans without touching string content."""

    output: list[str] = []
    index = 0
    quote = ""
    escaped = False
    while index < len(text):
        char = text[index]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "!" and index + 1 < len(text) and text[index + 1] in {"0", "1"}:
            output.append("true" if text[index + 1] == "0" else "false")
            index += 2
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _extract_json_assignments(text: str, marker: str) -> list[str]:
    values: list[str] = []
    start = 0
    while True:
        marker_index = text.find(marker, start)
        if marker_index < 0:
            break
        cursor = marker_index + len(marker)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != "=":
            start = cursor
            continue
        equal_index = cursor
        brace_index = equal_index + 1
        while brace_index < len(text) and text[brace_index].isspace():
            brace_index += 1
        # Only accept a direct object-literal assignment. Searching for the next
        # brace would misread Object.assign({}, ...) or Promise.resolve({...}).
        if brace_index >= len(text) or text[brace_index] != "{":
            start = equal_index + 1
            continue
        end_index = _find_json_object_end(text, brace_index)
        if end_index < 0:
            start = brace_index + 1
            continue
        values.append(text[brace_index:end_index])
        start = end_index
    return values


def _find_json_object_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _activity_task_from_page_task(task: dict[str, Any], task_id: str, group_label: str, group_index: int) -> dict[str, Any]:
    checkpoint = _first_dict(task.get("checkpoints"))
    checkpoint_progress = _first_dict(checkpoint.get("list") if checkpoint else None)
    indicator = _first_dict(task.get("indicators"))
    progress_source = indicator or checkpoint_progress or {}
    task_name = task.get("taskName") or task.get("task_name") or checkpoint.get("alias") or task_id
    award_name = task.get("awardName") or task.get("award_name") or checkpoint.get("awardname") or ""
    return {
        "task_id": task_id,
        "task_name": task_name,
        "award_name": award_name,
        "current": progress_source.get("cur_value"),
        "target": progress_source.get("limit"),
        "task_status": task.get("taskStatus") or task.get("task_status"),
        "counter": task.get("counter"),
        "group_label": group_label,
        "group_index": group_index,
    }


def _activity_checkpoint_tasks(
    task: dict[str, Any],
    *,
    parent_task_id: str,
    group_label: str = "",
    group_index: int | None = None,
) -> list[dict[str, Any]]:
    """Flatten Bilibili's multi-reward task template into claimable mission tasks.

    ``x/task/totalv2`` is queried with the parent ``task_id`` while
    ``x/activity_components/mission/receive`` expects each checkpoint ``sid``.
    The current activity page exposes those fields as ``taskId`` and
    ``checkpoints[].ztasksid`` respectively.
    """

    statistic_type = task.get("statistic_type")
    if statistic_type is None:
        statistic_type = task.get("statisticType")
    task_type = task.get("task_type")
    if task_type is None:
        task_type = task.get("taskType")
    checkpoints: Any = task.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        accumulative = task.get("accumulative_check_points")
        regular = task.get("check_points")
        if str(statistic_type) == "2" and isinstance(accumulative, list) and accumulative:
            checkpoints = accumulative
        else:
            checkpoints = regular
    if not isinstance(checkpoints, list):
        return []
    # 旧版“直接任务”虽然带一个 checkpoint，但领奖页仍使用父 taskId。
    # 新版多档奖励和非直接任务则使用 checkpoint sid/ztasksid。
    if len(checkpoints) == 1 and str(task_type) == "1" and str(statistic_type) != "2":
        return []

    parent_name = str(task.get("taskName") or task.get("task_name") or "").strip()
    parent_indicator = _first_dict(task.get("indicators"))
    flattened: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            continue
        task_id = str(
            checkpoint.get("ztasksid")
            or checkpoint.get("sid")
            or checkpoint.get("task_id")
            or checkpoint.get("taskId")
            or ""
        ).strip()
        if not task_id:
            continue
        progress = _first_dict(checkpoint.get("list"))
        # totalv2 的父任务 indicators[0].cur_value 是页面刷新按钮展示的权威观看分钟数。
        # checkpoint.list 主要提供各档目标；旧缓存可能仍带较小的 cur_value，不能覆盖父任务实时值。
        current_value = parent_indicator.get("cur_value")
        if current_value is None:
            current_value = progress.get("cur_value")
        task_name = str(checkpoint.get("alias") or parent_name or task_id).strip()
        award_name = str(
            checkpoint.get("awardname")
            or checkpoint.get("award_name")
            or task.get("awardName")
            or task.get("award_name")
            or ""
        ).strip()
        node: dict[str, Any] = {
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "task_name": task_name,
            "award_name": award_name,
            "award_sid": checkpoint.get("awardsid") or checkpoint.get("award_sid") or "",
            "current": current_value,
            "target": progress.get("limit"),
            "task_status": checkpoint.get("status"),
            "group_label": group_label,
        }
        if group_index is not None:
            node["group_index"] = group_index
        flattened.append(node)
    return flattened


def _normalize_activity_task_progress(
    progress: dict[str, Any],
    queried_task_ids: list[str],
) -> dict[str, Any]:
    """Normalize totalv2 parent tasks to one node per reward checkpoint."""

    normalized = dict(progress)
    raw_tasks = progress.get("list")
    if not isinstance(raw_tasks, list):
        normalized["tracking_task_ids"] = list(queried_task_ids)
        return normalized

    tasks: list[dict[str, Any]] = []
    tracking_task_ids: list[str] = []
    seen_tracking: set[str] = set()
    for queried_task_id in queried_task_ids:
        task_id = str(queried_task_id or "").strip()
        if task_id and task_id not in seen_tracking:
            seen_tracking.add(task_id)
            tracking_task_ids.append(task_id)
    seen_claim: set[str] = set()
    for raw_task in raw_tasks:
        if not isinstance(raw_task, dict):
            continue
        parent_task_id = str(
            raw_task.get("task_id")
            or raw_task.get("taskId")
            or raw_task.get("taskid")
            or ""
        ).strip()
        if parent_task_id and parent_task_id not in seen_tracking:
            seen_tracking.add(parent_task_id)
            tracking_task_ids.append(parent_task_id)
        checkpoint_tasks = _activity_checkpoint_tasks(
            raw_task,
            parent_task_id=parent_task_id,
            group_label=_task_date_label(raw_task),
        )
        if checkpoint_tasks:
            for checkpoint_task in checkpoint_tasks:
                claim_task_id = checkpoint_task["task_id"]
                if claim_task_id in seen_claim:
                    continue
                seen_claim.add(claim_task_id)
                tasks.append(checkpoint_task)
        elif parent_task_id not in seen_claim:
            seen_claim.add(parent_task_id)
            tasks.append(dict(raw_task))

    normalized["list"] = tasks
    normalized["tracking_task_ids"] = tracking_task_ids
    return normalized


def _task_date_label(task: dict[str, Any]) -> str:
    for key in ("group_label", "taskName", "task_name", "name", "title"):
        text = str(task.get(key) or "")
        match = re.search(r"(\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
        if match:
            return re.sub(r"\s+", "", match.group(1))
    return ""


def _extract_era_task_groups(state: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    top_level_groups = state.get("EraTasklistPc")
    if isinstance(top_level_groups, dict):
        top_level_groups = [top_level_groups]
    if isinstance(top_level_groups, list):
        for group in top_level_groups:
            if _is_task_group(group):
                groups.append(group)

    def visit(
        value: Any,
        panel_label: str = "",
        panel_period: dict[str, int] | None = None,
    ) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child, panel_label, panel_period)
            return
        if not isinstance(value, dict):
            return

        name = value.get("name")
        current_label = panel_label
        current_period = panel_period
        if name == "EvaTabs.Panel":
            current_label = _tab_panel_label(value) or panel_label
            current_period = _activity_period_from_panel(value) or panel_period
        if name == "EraTasklistPc":
            props = value.get("props") if isinstance(value.get("props"), dict) else {}
            if _is_task_group(props):
                group = dict(props)
                if current_label:
                    group.setdefault("group_label", current_label)
                if current_period:
                    group.update(current_period)
                groups.append(group)
            return
        for child in value.values():
            visit(child, current_label, current_period)

    visit(state)
    return groups


def _activity_period_from_panel(panel: dict[str, Any]) -> dict[str, int]:
    stack: list[Any] = [panel]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
            continue
        if not isinstance(node, dict):
            continue
        if node is not panel and node.get("name") == "EvaTabs.Panel":
            # A parent tab must not borrow the validity window from a nested tab.
            continue
        if node.get("name") != "EvaText":
            stack.extend(reversed(list(node.values())))
            continue
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        content = str(props.get("content") or "")
        if "有效统计时间" in content:
            period = _parse_activity_period(content)
            if period:
                return period
    return {}


def _parse_activity_period(text: str) -> dict[str, int]:
    match = re.search(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*"
        r"(\d{1,2})\s*[:：]\s*(\d{2})\s*[-~—–至]\s*"
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*"
        r"(\d{1,2})\s*[:：]\s*(\d{2})",
        text,
    )
    if not match:
        return {}
    values = [int(value) for value in match.groups()]
    try:
        start = datetime(*values[:5], tzinfo=BILIBILI_TIMEZONE)
        end = datetime(*values[5:], tzinfo=BILIBILI_TIMEZONE)
    except ValueError:
        return {}
    if end <= start:
        return {}
    return {
        "active_start": int(start.timestamp()),
        "active_end": int(end.timestamp()),
    }


def _iter_nested_dicts(value: Any) -> Iterator[dict[str, Any]]:
    """深度优先遍历嵌套容器，不复制整棵活动配置树。"""
    stack = [value]
    visited: set[int] = set()
    while stack:
        node = stack.pop()
        if not isinstance(node, (dict, list)):
            continue
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)
        if isinstance(node, dict):
            yield node
            children = node.values()
        else:
            children = node
        stack.extend(reversed(list(children)))


def _is_task_group(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    tasklist = value.get("tasklist")
    if not isinstance(tasklist, list):
        return False
    return any(isinstance(task, dict) and (task.get("taskId") or task.get("task_id")) for task in tasklist)


def _task_group_label(group: dict[str, Any], tab_labels: list[str], index: int) -> str:
    for key in ("group_label", "tabName", "tab_name", "date", "day", "title"):
        label = str(group.get(key) or "").strip()
        if label:
            return label
    return _group_label_for_index(tab_labels, index)


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    if isinstance(value, dict):
        return value
    return {}


def _is_terminal_claim_error(exc: Exception) -> bool:
    text = str(exc)
    return "csrf" in text.lower() or "请求频率过高" in text or "频率" in text or "稍后再试" in text


def _extract_tab_labels(state: Dict[str, Any]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()

    legacy_panels = state.get("EvaTabs.Panel") or []
    if isinstance(legacy_panels, dict):
        legacy_panels = [legacy_panels]
    if isinstance(legacy_panels, list):
        for panel in legacy_panels:
            label = _tab_panel_label(panel)
            if label and label not in seen:
                seen.add(label)
                labels.append(label)

    for node in _iter_nested_dicts(state):
        if node.get("name") != "EvaTabs.Panel":
            continue
        label = _tab_panel_label(node)
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def _tab_panel_label(panel: Any) -> str:
    if not isinstance(panel, dict):
        return ""
    props = panel.get("props") if isinstance(panel.get("props"), dict) else panel
    tab_item = props.get("tabItem") if isinstance(props.get("tabItem"), dict) else {}
    for key in ("tabItemProps", "activatedTabItemProps"):
        item_props = tab_item.get(key) if isinstance(tab_item.get(key), dict) else {}
        text_content = item_props.get("textContent") if isinstance(item_props.get("textContent"), dict) else {}
        label = str(text_content.get("content") or "").strip()
        if label:
            return label
    return ""


def _group_label_for_index(labels: list[str], index: int) -> str:
    if not labels:
        return f"第 {index + 1} 组"
    if index < len(labels):
        return labels[index]
    return labels[-1]
