# 守望先锋 B 站直播挂宝 / Overwatch Bilibili Live Drops Guard

当前版本：`v0.5.9`

开源地址：<https://github.com/taocihei/overwatch-bilibili-drops-guard>

## 重要声明

本软件完全免费。如果你是购买得到的，请立即联系商家退款。

赞助没有任何功能效果，不会解锁功能、不会提高成功率、不会获得优先支持，也不会影响掉宝或领奖结果。赞助只相当于给作者点了一次赞。

本工具只是本机辅助观看和检查任务状态。请自行遵守 B 站活动规则和账号使用规则。掉宝是否到账取决于 B 站活动规则、账号资格、直播间活动状态和平台接口变化。

## 这个软件做什么

这是一个给守望先锋 B 站直播掉宝活动使用的桌面工具。

它可以在你主动点击“自动获取 Cookie”时临时打开一个本机 Edge 或 Chrome 登录窗口；正式挂宝只运行当前应用，通过内部后台会话发送直播观看计时请求，不会打开任何直播间浏览器窗口或标签页。程序会定时检查直播间、任务进度和领奖状态，任务完成后可以自动领取，也可以手动点击领取。

默认直播间是守望先锋赛事直播间：`23612045`。

## 普通用户下载使用

1. 打开项目页面：<https://github.com/taocihei/overwatch-bilibili-drops-guard>
2. 进入右侧或页面中的 `Releases`。
3. 下载 `OverwatchBiliDrops-v0.5.9.exe`。
4. 双击运行。
5. 如果 Windows 提示“未知发布者”或“Windows 已保护你的电脑”，点击“更多信息”，再点“仍要运行”。这是个人开源软件常见提示，不代表一定有病毒。
6. 第一次使用先点“自动获取 Cookie”，在弹出的独立 Edge/Chrome 窗口里登录 B 站。
7. 登录成功后，软件会自动回填 Cookie 并关闭登录窗口。
8. 可以点“保存账号”保存当前 Cookie，之后用账号下拉切换。
9. 直播间默认已填好，直接点“开始挂宝”即可。
10. 在“观看进度”和“运行日志”里看还差多少分钟、是否已完成、是否已领取。

如果 `Releases` 里暂时没有安装包，说明作者还没有上传新版 EXE，可以按下面的“源码运行”方式启动。

## 界面怎么用

- `只打开登录页（手动）`：只帮你打开 B 站登录页，不自动读取 Cookie。适合排查浏览器打不开，或手动复制 Cookie。
- `自动获取 Cookie`：推荐使用。程序会打开独立 Edge/Chrome 自动获取窗口；请在这个窗口登录 B 站，成功后自动回填 Cookie。
- `账号与并行`：用来选择要挂宝的账号。获取 Cookie 后点“保存账号”，下次可直接勾选多个账号并行。
- `直播间房号`：默认 `23612045`。也可以粘贴完整直播间链接，保存后会自动变成数字房间号。
- `观看连接`：表示后台请求连接数，不等同于服务端有效倍率。界面会区分“心跳已接受”和 B 站真实入账，并用 `totalv2` 返回的 `indicators[0].cur_value` 估算实绩倍率。当前最多支持 `100` 路。
- `自动领奖`：开启后，任务满足条件会自动领取。领奖固定只用 1 个线程，避免请求太快失败。
- `任务 ID`：通常留空。程序会自动从活动页读取任务，不需要用户手填。自动识别失败时，可以按下面“手动获取直播间号和任务 ID”填写。
- `通知 URL`：可留空。填写后，启动、检测到可领取、领取成功、领取失败、Cookie 获取成功等关键事件会向该地址发送 JSON POST。
- `观看进度`：优先显示本次观看进度，比如“还差 48 分钟”“已完成，待领取”“已领取”。
- `运行日志`：保留登录、计时、任务识别和领取记录，适合排查异常。

## v0.5.9 新增与修复

- **100 路快启优化**：观看连接从逐秒单路启动改为每 0.5 秒一批，设置 100 路时约 5 秒内全部真实上线提交心跳。
- **减少 100 路启动资源抖动**：WBI 签名密钥改为进程级缓存，避免每路连接各自请求 `nav` 拉取签名参数，100 路启动更稳。
- **目标倍率与实绩同屏**：连接状态显示“目标 Nx / B 站实绩约 Mx”，验收口径只看 `totalv2` 的真实分钟数，避免把连接数误当成到账倍率。

## v0.5.8 新增与修复

- **实绩倍率响应更快**：从十分钟历史平均改为最近三分钟滚动结果，启动阶段的短暂突增不会继续污染当前倍率。
- **修复多账号实绩倍率丢失**：协调器会把各账号 `totalv2` 实绩传到“连接详情”，单账号显示一个倍率，多账号显示倍率范围。
- **统一连接状态语义**：多账号汇总同样显示“心跳已接受”，不再回退为容易误解的“路正常”。
- **优化停止与重启**：所有账号先同时收到停止信号，再共享两秒回收预算，减少后台线程与连接残留，并避免按账号逐个等待造成界面卡顿。

## v0.5.7 新增与修复

- **补齐账号删除入口**：每个账号行都提供“编辑”和“删除”，删除前使用统一的应用内确认弹窗。
- **修复账号编辑逻辑**：账号改名会替换原记录并继承勾选状态；清空 Cookie 后保存会明确阻止，不再静默删除已保存账号。
- **修复新增/切换状态**：新增、编辑、删除后立即刷新“正在编辑”状态，删除当前账号时选择相邻账号。
- **区分请求成功与服务端入账**：`40/40` 改为“心跳已接受”，不再把 HTTP `code=0` 显示成多路计时有效。
- **显示 B 站实绩倍率**：根据 `totalv2` 的真实进度滚动计算服务端有效倍率；现场长期复核确认 v0.5.6 的 40 路请求最终仍会被 B 站合并到约 `1.0x`。
- **修复连接详情不可达**：默认窗口的运行状态卡新增“连接详情”入口，完整明细使用可移动的应用内弹窗显示。

## v0.5.6 新增与修复

- **修复多线程只按约 1 倍累计**：不再覆盖账号原有的 `LIVE_BUVID`、`buvid3`、`buvid4` 和指纹 Cookie；每路只隔离 x25Kn 的 `AUTO...` 身份与页面 UUID，避免 Cookie 身份冲突导致连接被合并。
- **对齐当前 x25Kn 协议**：E/X 心跳改为完整 WBI 签名 query POST，补齐 `ruid`、`trackid`、`web_location`、`csrf`，并使用当前直播页请求头和进入房间动作。
- **排除会互相干扰的播放器心跳**：官方 `te9Kl/s82Tq` 已完成真实接口对照；与多路 x25Kn 同时提交时，5 路首周期反而只增加 3 分钟，因此正式计时保持竞品已验证的独立 x25Kn 链路。
- **真实 5 路验证**：首个完整 60 秒周期从 142 增至 147；两个周期 5 路请求全部成功，服务端显示 151（其中 1 分钟延迟入账），已排除原先 78 秒只增加 2 分钟的问题。
- **保留真实进度口径**：界面仍只显示 B 站 `totalv2` 返回的真实分钟数，不用本地请求次数虚构倍速。

## v0.5.5 新增与修复

- **以 B 站真实分钟数为准**：直接读取 `totalv2 → data.list[*].indicators[0].cur_value`，多线程心跳成功次数不再冒充已累计时长。
- **修复多档奖励显示错位**：完成 60 分钟档后，界面会自动显示下一档（例如 `84 / 120 分钟`），不再停留在首档 `84 / 60`。
- **修复关闭自动领奖后进度不刷新**：任务发现和 `totalv2` 轮询始终运行，自动领奖开关只控制是否提交领取。
- **修复进度回退与数据源互相覆盖**：活动任务优先使用 `totalv2`；临时空值或旧值不会覆盖最后一次 B 站已确认分钟数。
- **合并重复登录账号**：同一 B 站 UID 即使保存成多个名称，本次运行也只启动一组，避免重复请求和本地统计翻倍。
- **统一观看连接语义**：线程卡改为“观看连接”，心跳次数只表示连接健康；本地运行时长按真实墙钟计算，不再按并发请求间隔相加。
- **重做应用内弹窗**：赞助、帮助、详情、关于和支付结果统一为可拖动、居中的应用风格弹窗；选择金额后自动生成二维码。

## v0.5.4 新增与修复

- **赞助服务开箱即用**：桌面端已内置公开 HTTPS 服务地址，无需再手动配置环境变量；仍支持环境变量覆盖，方便本地调试或迁移。
- **完成 YunGouOS 生产接入**：商户号和支付密钥仅保存在服务端加密环境变量中，客户端与 GitHub 源码均不包含凭据。
- **改用支付回调确认结果**：服务端校验 YunGouOS 回调签名、订单号和金额后才标记支付成功，桌面端只查询本服务状态，不再高频查询支付平台。
- **增加二维码安全代理**：二维码统一通过本站 HTTPS 返回，并限制上游域名和图片类型，避免客户端加载不安全地址。

## v0.5.3 新增与修复

- **新增低打扰赞助入口**：页脚只保留一个“支持作者”入口，不在启动、挂宝或领奖过程中弹窗；赞助完全自愿且不影响任何功能。
- **接入 YunGouOS 二维码流程**：可选择 ¥3 / ¥6 / ¥10，生成微信扫码支付二维码并轮询订单状态；确认付款后显示 QQ 群 `1012969672` 并支持一键复制。
- **减少界面重复信息**：版本号只在窗口标题显示，移除品牌区和页脚的重复版本；页脚也不再重复直播间号和任务 ID。
- **修复运行日志重复刷屏**：相同任务摘要即使跨过检查周期也只记录一次，只有进度或状态实际变化时才追加日志。

## v0.5.2 修复

- **修复新版活动页误报“暂无掉宝任务”**：兼容 B 站将 `window.__BILIACT_EVAPAGEDATA__` 从标准 JSON 改成 JavaScript 对象字面量的输出方式。
- **兼容压缩布尔值**：可解析活动页里的 `!0` / `!1`，并确保字符串内容不会被误替换。
- **修复跨午夜选错活动日期**：以 B站响应的服务器时间为时钟基准，再按页面公布的 UTC+8“有效统计时间”选择当前任务组；不受电脑本地时区影响，也不会在午夜提前显示尚未开始的下一日奖励。
- **明确显示任务有效期**：日志同时显示 B站当前时间，以及当前活动任务实际有效到几点，避免把“B站已经过午夜”和“下一日任务已经开始”混为一谈。
- **实时页面验证**：房间 `23612045` 当前页面可重新识别 8 个父进度任务和 38 个 checkpoint 领取任务。

## v0.5.1 修复

- **适配 B 站新版多档奖励模板**：进度查询继续使用父任务 ID，每一档奖励改用对应 checkpoint `sid/ztasksid` 领取，不再拿父任务 ID 请求领奖接口。
- **修复多档奖励只显示一档**：按 60/120/180/240/300 分钟等 checkpoint 分别显示奖励、进度和领取状态。
- **修复满进度误判可领**：checkpoint 状态为 1 时即使分钟数已满也会等待 B 站刷新，只有状态 2 才进入领奖队列，状态 3 视为已领取。
- **兼容旧领取模板**：无 checkpoint、旧版直接任务和新版单/多 checkpoint 模板可同时识别。
- **增强任务页签识别**：兼容嵌套 `EvaTabs.Panel`，按实际活动日期关联同一天的多个奖励组件。

## v0.5.0 修复

- **修复开播状态切换**：后台计时会采用最新直播间状态，未开播后开播可自动恢复，下播后不再继续提交旧心跳。
- **修复重复领奖与并发竞态**：同一任务只排队、提示和提交一次；已领取或状态回退的任务会及时移出待领取队列。
- **修复登录状态假运行**：Cookie 明确失效时不会启动后台计时，界面会结束“运行中”状态。
- **修复小窗口操作不可达**：左侧凭据区域支持滚动，窄窗口会压缩顶部工具栏，账号与领奖按钮均可操作。
- **增强 Cookie 与资源安全**：过滤仿冒域、稳定选择同名 Cookie，并关闭所有短生命周期网络会话。
- **增强打包可靠性**：缺少 Selenium、Pillow、Tkinter 等依赖或 PyInstaller 失败时立即终止，不再生成残缺安装包或误报成功。

## v0.4.9 修复

- **增强 40 路后台心跳**：每路使用独立会话身份并补充网页心跳，减少多路计时被合并或降速的情况。
- **只显示 B 站真实进度**：移除界面里的本地估算进度，任务进度以 B 站接口返回为准。
- **拆分运行日志**：新增任务日志、房间日志、全部日志切换，清空日志后仍会继续写入新日志。
- **优化窗口缩放**：默认窗口下压缩卡片和日志区域，减少内容被截断、按钮大小不一致的问题。
- **下掉大神入口**：暂不接入网易大神掉宝，当前版本继续专注 B 站后台挂宝。

## v0.4.8 修复

- **修复活动进度长期空返回**：`x/task/totalv2` 不返回可显示进度时，不再连发 `mission/info`，改用本地挂机时长估算剩余分钟，避免触发“操作太快”。
- **增强任务字段解析**：兼容 `cur_value`、`limit`、`currentValue` 等更多进度字段，避免接口有数据但解析不到。
- **优化等待状态显示**：活动进度空返回显示为等待同步，不再误导成网络异常。

## v0.4.7 修复

- **补任务进度空返回日志**：活动任务已识别但接口暂未返回当前分钟数时，会定时写明状态，不再静默。
- **减少后台计时日志积压**：多路观看时不再逐路刷代理/网络失败详情，只保留汇总状态，避免淹没任务日志。
- **修复 Cookie 状态死文案**：Cookie 状态改为随内容和登录结果更新，登录正常后显示“Cookie 已登录”。

## v0.4.6 修复

- **修复任务识别后的界面空窗**：过滤错误全 0 快照时，会立即显示“任务已识别，等待 B 站同步当前分钟数”，不再像没有任务。
- **保持当前分钟数优先**：B 站返回奖励分钟数后仍会覆盖等待状态，不再显示启动期错误 0/30 明细。

## v0.4.5 修复

- **修复任务进度重叠**：启动时会过滤 B 站先返回的全 0 低置信任务快照，避免日志先显示 0/30 再立刻跳到当前分钟数。
- **修复领取结果卡片状态**：可领取、未到领取条件、领取中、已领取、已跳过和领取失败都会跟随实际日志更新，不再停在固定文案。
- **优化窗口缩放**：降低固定最小宽高和卡片高度，左右区域随窗口宽度分配，缩小窗口时不再大面积截断。

## v0.4.4 修复

- **修复手动任务 ID 分隔符问题**：现在任务 ID 支持英文逗号、中文逗号、分号、空格和换行混合输入，保存后会统一整理为英文逗号。
- **优化主界面布局**：默认直播间号、任务 ID 自动获取、登录凭据流程、观看进度、运行状态和运行日志的位置更清楚。
- **优化运行日志区域**：日志区域占比更大，并增加“清空日志”“复制日志”“自动滚到最新”等常用操作。
- **优化打包稳定性**：打包时会带上 Tkinter 和界面圆角绘制需要的依赖，减少不同电脑上的显示差异。

## v0.4.2 修复

- **进一步修复多路计时**：v0.4.1 只改了请求体里的 device 字段，但 B 站去重看的是 Cookie header 里的 `buvid3`。本版本同时覆盖 cookie 里的 `buvid3` 让每路 worker 真正成为独立设备。
- **入房前先注册直播间进入动作**：后台计时会先注册进入直播间，再发送观看心跳，保证观看时长能被 B 站正确累计。
- **错开启动后台计时**：从瞬时并行启动改为每秒启动一路，避免短时间大量请求触发 B 站频控。20 路启动需要约 20 秒。

## v0.4.1 修复

- **多路后台计时实际只算 1 路的 bug**：之前所有后台线程共用 Cookie 里的 `buvid3`，B 站把它们去重成一个会话，导致开 20 路反而比一路浏览器还慢。现在每个 worker 启动时生成独立的 `live_buvid` 和 `device_uuid` 作为该会话的设备身份，B 站会把每路当作独立设备分别累计观看时长。

## v0.4.0 新增

- `看上手指引`：顶部声明条右侧的链接，点开是 4 步入门 modal。
- `任务进度刷新按钮`：任务进度卡右上角"↻ 刷新"，开始挂宝后才能用。
- `重新识别任务按钮`：任务进度卡右上角"↻ 重新识别任务"，清掉缓存重新拉活动任务。
- `后台计时状态卡`：右栏中间区域，默认折叠显示"X/Y 正常"汇总；点"展开查看每路"看每条后台线程的状态（编号、状态、下一次心跳秒数、错误原因）。

## 手动获取直播间号和任务 ID

大多数情况下不需要手动获取，软件会自动识别。只有出现“任务进度一直无数据”“任务 ID 获取失败”时，再按这里操作。

### 手动获取直播间号

1. 打开有当前掉宝活动的 B 站直播间。
2. 复制浏览器地址栏里的链接。
3. 取 `live.bilibili.com/` 后面的数字。

例如：

```text
https://live.bilibili.com/23612045?live_from=82002
```

软件里只需要填写：

```text
23612045
```

### 手动获取任务 ID

任务 ID 用来查询任务进度和领取奖励。通常软件会自动从直播页读取；如果自动失败，可以手动复制。

1. 用浏览器登录 B 站。
2. 打开有当前掉宝任务的直播间或活动页面。
3. 按 `F12` 打开开发者工具。
4. 切到 `网络 / Network`。
5. 刷新页面，搜索 `totalv2` 或 `task`。
6. 找到类似下面的请求：

```text
https://api.bilibili.com/x/task/totalv2?task_ids=...
```

7. 复制 `task_ids=` 后面的内容，粘贴到软件的“任务 ID”输入框。
8. 如果有多个任务 ID，可以用英文逗号、中文逗号、分号、空格或换行分隔，保存后软件会自动整理。

任务 ID 通常长这样：

```text
6ERAcwloghvqrb00,6ERAcwloghvqnk00,6ERAcwloghvql500
```

如果找不到 `totalv2` 请求，也可以在页面源码或网络响应里搜索 `taskId`，复制对应的任务 ID。

## 常见问题

### 1. 点“自动获取 Cookie”没有读取到 Cookie

先点“只打开登录页（手动）”测试本机 Edge/Chrome 是否能正常打开 B 站。这个手动页面不会自动读取 Cookie。

如果仍然失败，请确认电脑已安装 Edge 或 Chrome，并关闭可能拦截浏览器启动的安全软件。

### 2. 已经登录 B 站，但软件还是说 Cookie 获取失败

重新点一次“自动获取 Cookie”，确认是在弹出的独立自动获取窗口里登录 B 站后等几秒。

如果还是失败，可以手动复制 Cookie。Cookie 至少需要包含 `SESSDATA`，领奖通常还需要 `bili_jct`。缺少 `bili_jct` 时，软件会提示重新获取 Cookie。

### 3. 任务进度一直不变

先确认直播间正在直播，并且活动规则允许当前账号参与掉宝。

如果刚开始挂宝，请等待一个检查周期。默认每 10 秒检查一次。多开后台观看线程后，进度也需要等 B 站接口刷新，不会每秒变化。

### 4. 怎么切换多个账号

每个账号都需要单独获取一次 Cookie。

1. 在“账号名称”里填一个好记的名字，比如 `主账号`。
2. 点“自动获取 Cookie”，在弹出的独立自动获取窗口里登录对应 B 站账号。
3. 回填成功后点“保存账号”。
4. 换另一个账号名，重复获取并保存。
5. 之后在“账号与并行”里勾选要挂宝的账号即可。

切换或增减账号后，如果正在挂宝，请先停止再重新开始。已经运行中的后台计时会继续使用启动时的账号 Cookie。

### 5. 通知 URL 怎么用

通知 URL 是给进阶用户或自用机器人用的 Webhook。留空不影响使用。

程序会发送 `POST` 请求，内容是 JSON：

```json
{
  "title": "守望先锋 B 站直播挂宝",
  "message": "已领取：某个奖励",
  "level": "info",
  "source": "OverwatchBiliDrops"
}
```

如果通知发送失败，只会写入运行日志，不会影响挂宝和领奖。

### 6. 软件提示“还差多少分钟”和 B 站页面不一致

B 站活动页和接口刷新可能有延迟。可以等待 1 到 2 个检查周期，或者停止后重新开始。

后续活动可能按日期、页签、任务批次更新，软件会尝试每次检查时重新读取最新任务，不会把任务日期写死。

### 7. 显示“已完成，待领取”，但没有立刻领取

自动领奖开启时，软件会按顺序一个一个领取。领奖固定只用 1 个线程。

如果 B 站提示操作太快，软件会等待后自动重试。你也可以稍后点“领取奖励”手动再试。

### 8. 领取失败，提示重新获取 Cookie

这通常表示登录信息过期、不完整，或者 Cookie 里缺少 `bili_jct`。

点“自动获取 Cookie”重新登录一次，然后再开始挂宝或点击领取。

### 9. 领取失败，提示 B 站操作太快

这是 B 站限频。不要连续点领取。等待一会儿后再试，软件也会自动放慢领取速度。

### 10. 打开软件闪退

到下面目录查看错误日志：

```text
%APPDATA%\OverwatchBiliDrops\crash.log
```

把 `crash.log` 内容发到 GitHub Issues，或者发给作者定位。

### 11. 杀毒软件报毒

这是 Python + PyInstaller 打包的单文件 EXE，个人开源软件可能被误报。你可以从源码运行，或自行查看代码后本地打包。

## 源码运行

需要电脑已安装 Python 3.11 或更新版本。

```powershell
python -m pip install -r requirements.txt
python app.py
```

## 自己打包

```powershell
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

打包后的程序在：

```text
dist\OverwatchBiliDrops.exe
```

发布时会同时生成带版本号的文件，例如：

```text
dist\OverwatchBiliDrops-v0.5.9.exe
```
## 赞助

如果这个工具帮到了你，可以点击软件页脚的“支持作者”，选择金额后生成 YunGouOS 微信支付二维码。付款成功后，界面会显示 QQ 群 `1012969672`。

赞助完全自愿，不解锁功能、不提高成功率、不提供优先支持，也不影响挂宝或领奖结果。

> 维护者配置：桌面端默认连接项目公开赞助服务；如需本地调试或迁移，可用环境变量 `BILI_DROPS_SPONSOR_API_URL` 覆盖。商户密钥只保存在服务端环境变量中；服务端负责调用 YunGouOS `nativePay`、验证支付回调，并提供 `/orders` 创建订单及 `/orders/{order_id}` 状态查询接口。

---

# English Guide

Project name: **守望先锋 B 站直播挂宝 / Overwatch Bilibili Live Drops Guard**

Version: `v0.5.9`

Repository: <https://github.com/taocihei/overwatch-bilibili-drops-guard>

This software is completely free. If you paid for it, please ask the seller for a refund.

Sponsorship has no functional effect. It does not unlock features, improve success rate, provide priority support, or affect drop/reward results. It is only a way to give the author a thumbs-up.

## What It Does

This is a Windows desktop helper for Overwatch Bilibili live drop tasks.

It opens one temporary local Edge or Chrome login window only when you explicitly request automatic Cookie capture. During guarding, only the desktop app runs: internal background sessions submit live heartbeat requests, check task progress, and claim completed rewards. No live-room browser window or tab is opened.

Default room: `23612045`.

## Download And Use

1. Open the repository page: <https://github.com/taocihei/overwatch-bilibili-drops-guard>
2. Open `Releases`.
3. Download `OverwatchBiliDrops-v0.5.9.exe`.
4. Double-click to run it.
5. If Windows shows an unknown-publisher warning, click `More info`, then `Run anyway`.
6. Click `自动获取 Cookie`, then sign in to Bilibili in the independent Edge/Chrome window opened by the app.
7. The app will fill the Cookie automatically after login.
8. Click `保存账号` if you want to keep this account profile and switch accounts later.
9. Keep the default room or enter another live-room ID/URL.
10. Click `开始挂宝`.
11. Check `观看进度` and `运行日志` for remaining minutes, claimable rewards, and claimed rewards.

## Common Problems

- Browser does not open: make sure Edge or Chrome is installed, then try `只打开登录页（手动）`.
- Cookie capture fails: click `自动获取 Cookie` again and sign in inside the independent capture window. Reward claiming usually requires `bili_jct`.
- Multiple accounts: capture Cookie once for each account, name it, then select accounts in `账号与并行`.
- Notification URL: optional webhook. The app sends JSON POST messages for important events such as claim success or failure.
- Progress does not change: wait for one or two check intervals and confirm the live room is active.
- Claim fails because requests are too frequent: wait and retry later. The app slows down automatic claiming.
- App crashes: check `%APPDATA%\OverwatchBiliDrops\crash.log` and report it in GitHub Issues.
- Antivirus warning: PyInstaller single-file apps may be falsely flagged. You can inspect the source and run from source.

## Run From Source

```powershell
python -m pip install -r requirements.txt
python app.py
```

## Build

```powershell
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The executable will be generated at:

```text
dist\OverwatchBiliDrops.exe
```

Release builds are also copied with a versioned file name, for example:

```text
dist\OverwatchBiliDrops-v0.5.9.exe
```
