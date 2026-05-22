# 守望先锋 B 站直播挂宝

> 中文说明在前，English guide below.

这是一个自用的 B 站直播掉宝守护工具。它通过本机 Edge/Chrome 登录获取 B 站 Cookie，再打开一个或多个直播窗口累计观看时长，并定时检查直播间、任务进度和领奖状态。

本项目不会内置账号，不破解客户端，不承诺绕过平台风控。实际掉宝是否到账取决于 B 站活动规则、账号资格、直播间活动状态和平台接口变化。

## 功能

- 图形界面填写 Cookie、直播间号、检查间隔和直播窗口数。
- 支持自动拉起 Edge/Chrome 的 B 站登录页并读取登录 Cookie。
- 支持手动粘贴 Cookie。
- 获取 Cookie 后会写入自动化浏览器，用真实直播页面观看。
- “直播窗口数”用于并行累计直播观看时长。
- 定时检查登录状态、直播间状态和掉宝任务进度。
- 任务 ID 可留空，程序会尽量自动识别；也可以手动填写。
- 勾选“自动领奖”后，识别到完成任务会固定用 1 个领奖线程提交请求。
- 实时日志显示 Cookie、直播窗口、任务检查和领奖结果。

## 安装与运行

```powershell
python -m pip install -r requirements.txt
python app.py
```

## 使用方法

1. 启动程序：`python app.py`。
2. 点击“自动获取 Cookie”，在打开的 Edge/Chrome 里登录 B 站。
3. 如果自动获取失败，可以点“只打开登录页”，确认本机浏览器能正常打开 B 站。
4. 填入直播间号或直播间链接，例如 `123456` 或 `https://live.bilibili.com/123456`。
5. 设置“直播窗口数”。窗口数越多，程序会打开越多直播窗口，用于并行累计观看时长。
6. 按需勾选“自动领奖”。
7. 点击“开始挂宝”。
8. 需要手动领奖时，点击“领取奖励”。领奖请求固定只使用 1 个线程。

## Cookie 获取提示

推荐优先使用“自动获取 Cookie”。程序会优先尝试 Edge，再尝试 Chrome。浏览器打开后完成 B 站登录，程序检测到 `SESSDATA` 后会自动回填并保存 Cookie。

如果自动获取失败，也可以在已登录 B 站的浏览器里打开 `https://www.bilibili.com`，按 `F12` 打开开发者工具，在 Network 请求里复制 Cookie 请求头。Cookie 至少需要包含 `SESSDATA`，通常还需要 `bili_jct` 才能提交领奖请求。

## 任务 ID 说明

任务 ID 输入框可以留空。程序会先调用任务进度接口，自动识别响应里的 `task_id`、`taskId` 或 `id`。只有当活动接口不返回任务 ID，或自动识别不稳定时，才需要手动填写任务 ID。

多个任务 ID 可以用空格、逗号或分号分隔。

## 打包

```powershell
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

打包后的程序会生成在 `dist\OverwatchBiliDrops.exe`。

## 赞助

如果这个工具帮到了你，可以扫码赞助。

**赞助没有任何功能效果，不会解锁功能、不会提高成功率、不会获得优先支持，也不会影响掉宝或领奖结果。它只相当于你给作者点了一次赞。**

二维码已核对：左侧是支付宝，右侧是微信。

![赞助二维码](assets/sponsor.jpg)

## 免责声明

本工具仅用于本机自动化辅助观看和任务状态检查。请自行遵守 B 站活动规则和账号使用规则。平台接口、活动规则或风控策略变化可能导致功能失效。

---

# Overwatch Bilibili Live Drops Guard

This is a personal desktop helper for Bilibili live drop tasks. It uses the local Edge/Chrome browser to sign in and capture Bilibili cookies, opens one or more live-room windows to accumulate watch time, and periodically checks room status, task progress, and reward claiming state.

This project does not bundle accounts, does not crack any client, and does not promise to bypass platform risk controls. Whether rewards arrive depends on Bilibili activity rules, account eligibility, live-room status, and platform API changes.

## Features

- GUI for Cookie, live room, check interval, and live window count.
- Automatically opens Edge/Chrome for Bilibili login and reads the login Cookie.
- Supports manually pasted Cookie headers.
- Writes Cookie into an automated browser and watches the real live page.
- Uses multiple live windows to accumulate watch time in parallel.
- Periodically checks login state, live-room state, and drop task progress.
- Task IDs can be left empty for auto-discovery, or filled manually.
- Auto-claim uses exactly one reward-claiming thread.
- Realtime logs for Cookie capture, browser windows, task checks, and reward claiming.

## Install And Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

## Usage

1. Start the app: `python app.py`.
2. Click "自动获取 Cookie" and sign in to Bilibili in the opened Edge/Chrome window.
3. If automatic Cookie capture fails, click "只打开登录页" to verify that the local browser can open Bilibili.
4. Enter a room ID or live-room URL, such as `123456` or `https://live.bilibili.com/123456`.
5. Set the live window count. More windows mean more live pages opened to accumulate watch time in parallel.
6. Enable auto-claim if needed.
7. Click "开始挂宝".
8. To claim manually, click "领取奖励". Reward claiming always uses one thread.

## Cookie Notes

The recommended path is automatic Cookie capture. The app tries Edge first, then Chrome. After you sign in to Bilibili, the app detects `SESSDATA`, fills the Cookie field, and saves it automatically.

If automatic capture fails, open `https://www.bilibili.com` in a browser where you are already signed in, press `F12`, and copy the Cookie request header from a Network request. The Cookie must include at least `SESSDATA`; `bili_jct` is usually required for reward-claiming requests.

## Task ID Notes

The Task ID field can be left empty. The app tries to discover `task_id`, `taskId`, or `id` from the task-progress API response. Fill Task IDs manually only when the activity API does not return them or auto-discovery is unstable.

Multiple Task IDs can be separated by spaces, commas, or semicolons.

## Build

```powershell
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The packaged executable is generated at `dist\OverwatchBiliDrops.exe`.

## Sponsorship

If this tool helped you, you may scan the QR codes to sponsor the author.

**Sponsorship has no functional effect. It does not unlock features, improve success rates, grant priority support, or affect drops/reward claiming in any way. It is simply the equivalent of giving the author a like.**

The QR codes have been checked: Alipay is on the left, WeChat Pay is on the right.

![Sponsor QR codes](assets/sponsor.jpg)

## Disclaimer

This tool is only a local automation helper for watching live rooms and checking task state. Please follow Bilibili activity rules and account rules. Platform API changes, activity rule changes, or risk-control changes may break functionality.
