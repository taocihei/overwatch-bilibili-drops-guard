from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import base64
import urllib.parse
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

    def discover_live_activity_tasks(self, room_id: str) -> Dict[str, Any]:
        normalized_room_id = normalize_room_id(room_id)
        if not normalized_room_id:
            raise RuntimeError("直播间号格式不正确，无法自动获取任务 ID")

        response = self.session.get(f"https://live.bilibili.com/{normalized_room_id}", timeout=15)
        response.raise_for_status()
        match = re.search(
            r"window\.__initialState\s*=\s*(\{.*?\})\s*;\s*window\.__BILIACT_PAGEINFO__",
            response.text,
            re.S,
        )
        if not match:
            raise RuntimeError("直播页没有找到活动任务配置")

        state = json.loads(match.group(1))
        tasks: list[dict[str, Any]] = []
        seen: set[str] = set()
        tab_labels = _extract_tab_labels(state)
        task_groups = state.get("EraTasklistPc") or []
        for group_index, group in enumerate(task_groups):
            if not isinstance(group, dict):
                continue
            group_label = _group_label_for_index(tab_labels, group_index)
            for task in group.get("tasklist") or []:
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("taskId") or task.get("task_id") or "").strip()
                if not task_id or task_id in seen:
                    continue
                seen.add(task_id)
                indicator = _first_dict(task.get("indicators"))
                checkpoint = _first_dict(task.get("checkpoints"))
                checkpoint_progress = _first_dict(checkpoint.get("list") if checkpoint else None)
                progress_source = indicator or checkpoint_progress or {}
                task_name = task.get("taskName") or task.get("task_name") or checkpoint.get("alias") or task_id
                award_name = task.get("awardName") or checkpoint.get("awardname") or ""
                tasks.append(
                    {
                        "task_id": task_id,
                        "task_name": task_name,
                        "award_name": award_name,
                        "current": progress_source.get("cur_value"),
                        "target": progress_source.get("limit"),
                        "task_status": task.get("taskStatus"),
                        "counter": task.get("counter"),
                        "group_label": group_label,
                        "group_index": group_index,
                    }
                )
        return {"tasks": tasks, "groups": [{"label": label, "index": index} for index, label in enumerate(tab_labels)]}

    def get_activity_task_progress(self, task_ids: list[str]) -> Dict[str, Any]:
        normalized_task_ids = [str(task_id).strip() for task_id in task_ids if str(task_id).strip()]
        if not normalized_task_ids:
            return {"list": []}
        return self._get_data(
            "https://api.bilibili.com/x/task/totalv2",
            params={
                "task_ids": ",".join(normalized_task_ids),
                "need_all_invited_info": "false",
            },
        )

    def get_activity_mission_info(self, task_id: str) -> Dict[str, Any]:
        params = self._wbi_signed_params({"task_id": task_id})
        return self._get_data("https://api.bilibili.com/x/activity_components/mission/info", params=params)

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
    for panel in state.get("EvaTabs.Panel") or []:
        if not isinstance(panel, dict):
            continue
        tab_item = panel.get("tabItem") if isinstance(panel.get("tabItem"), dict) else {}
        props = tab_item.get("tabItemProps") if isinstance(tab_item.get("tabItemProps"), dict) else {}
        text_content = props.get("textContent") if isinstance(props.get("textContent"), dict) else {}
        label = str(text_content.get("content") or "").strip()
        if label:
            labels.append(label)
    return labels


def _group_label_for_index(labels: list[str], index: int) -> str:
    if not labels:
        return f"第 {index + 1} 组"
    if index < len(labels):
        return labels[index]
    return labels[-1]
