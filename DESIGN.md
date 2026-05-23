# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-05-23
- Primary product surfaces: Tkinter 桌面主窗口、Cookie 获取流程、直播观看配置、任务进度、运行日志、领奖操作。
- Evidence reviewed: `bili_drop_guard/gui.py`、`bili_drop_guard/config.py`、`README.md`、`tests/`、`.omx/artifacts/ui-top-guide-v2.png`。

## Brand
- Personality: 专业、清晰、温和，像一个可信赖的本地任务助手，而不是工程样板控制台。
- Trust signals: 版本号明确、当前运行状态常驻、关键流程有清楚说明、任务进度和运行日志分开、危险操作视觉上克制但可识别。
- Avoid: 左侧长说明挤压操作区、工业风直角表单、系统默认 Spinbox/Scrollbar 的硬质外观、含糊的“线程”文案、过度装饰和花哨渐变。

## Product goals
- Goals: 让用户快速完成 Cookie 获取、确认直播间、设置后台观看线程数、启动后台心跳计时、查看任务进度、自动或手动领奖。
- Non-goals: 不做账号托管、不内置账号、不在正式挂宝时打开直播间浏览器窗口、不把后台观看线程误导为多线程领奖。
- Success signals: 用户打开后能一眼知道 4 步流程；默认直播间和任务 ID 规则清楚；中间操作区空间充足；任务进度与日志互不干扰；按钮状态容易判断。

## Personas and jobs
- Primary personas: 个人开发者/产品经理，理解业务目标但不希望阅读技术细节。
- User jobs: 打开本地工具、登录 B 站、用后台直播心跳累计观看时长、满足条件后领取奖励。
- Key contexts of use: Windows 桌面、Edge/Chrome、本地源码运行、PyInstaller exe 打包运行。

## Information architecture
- Primary navigation: 单页工具，无多级导航。
- Core routes/screens: 顶部标题状态区、顶部横向使用说明、下方左侧操作区、下方右侧反馈区。
- Content hierarchy: 标题与运行状态最高；横向说明只负责一次性教学；Cookie 和直播任务为主要操作；底部固定动作按钮；右侧固定展示任务进度和运行日志。

## Design principles
- Principle 1: 教学信息上移，操作空间下沉。说明区横向展示，不再占用长期操作宽度。
- Principle 2: 高频操作优先。Cookie、直播间、后台观看线程数、自动领奖、任务 ID 必须在默认窗口内完整可见。
- Principle 3: 业务语义优先于技术术语。用“后台观看线程数”解释并行心跳观看；用“领奖固定 1 个线程”解释领取行为。
- Tradeoffs: 保持 Tkinter 原生和轻量打包，不引入新 UI 依赖；通过自绘圆角卡片、胶囊按钮、步进器、间距和文案提升质感。

## Visual language
- Color: 温暖浅灰背景，奶白卡片，鼠尾草绿作为主操作色，浅红只用于停止，低饱和米色用于说明区。
- Typography: Windows 上使用 `Microsoft YaHei UI`；页面标题醒目但不过大；卡片标题清楚；说明文字短句换行。
- Spacing/layout rhythm: 8/12/16/24 节奏；顶部说明卡片等宽；下方操作区和反馈区保持稳定两栏比例。
- Shape/radius/elevation: 18-26px 圆角卡片、胶囊按钮、柔和边框和极浅阴影，避免硬直角。
- Motion: 不做装饰动画；状态变化通过状态标签、进度文本和日志体现。
- Imagery/iconography: 当前不引入图片资产；按钮用清晰动词，减少图标学习成本。

## Components
- Existing components to reuse: `RoundedPanel`、`PillButton`、`Stepper`、`tk.Text`、`tk.Entry`、`ttk.Frame`、`ttk.Label`。
- New/changed components: 顶部横向使用说明、并排 Cookie/直播任务操作卡、固定底部动作栏、独立任务进度卡、独立运行日志卡。
- Variants and states: 主按钮、次按钮、危险按钮、自动领奖开启/关闭、状态徽标、文本输入、只读进度、只读日志。
- Token/component ownership: 颜色、字体、间距和控件风格集中在 `bili_drop_guard/gui.py`，不新增主题文件。

## Accessibility
- Target standard: 面向桌面工具的基础可读性和键盘可用性。
- Keyboard/focus behavior: 输入框和文本框保留键盘编辑能力；按钮可鼠标点击；不依赖 hover 才能理解功能。
- Contrast/readability: 主文字深色，说明文字中灰，主按钮白字绿底，停止按钮红字浅红底。
- Screen-reader semantics: 关键说明使用真实文本，不用图片替代。
- Reduced motion and sensory considerations: 无闪烁、无自动动画。

## Responsive behavior
- Supported breakpoints/devices: Windows 桌面窗口，默认 1200x900，最小 1080x820。
- Layout adaptations: 顶部说明横向排列；下方左侧为操作区，右侧为反馈区；窗口缩小时保持主要控件完整可见。
- Touch/hover differences: 面向鼠标键盘；hover 只作为轻反馈，不承载信息。

## Interaction states
- Loading: Cookie 获取时状态显示“正在获取 Cookie”，日志提示正在打开 Edge/Chrome。
- Empty: Cookie 可为空但启动会提示；任务 ID 可为空代表自动识别。
- Error: Cookie 获取失败、浏览器打开失败、缺少配置等通过弹窗和日志反馈。
- Success: Cookie 获取成功后回填保存；开始、停止、领奖动作进入日志。
- Disabled: 当前版本不禁用按钮，重复点击由业务逻辑拦截并写日志。
- Offline/slow network, if applicable: 接口失败、登录超时、浏览器连接失败写入运行日志。

## Content voice
- Tone: 直接、温和、少术语。
- Terminology: “Cookie”“直播间号”“后台观看线程数”“任务 ID”“自动领奖”“任务进度”“运行日志”。
- Microcopy rules: 每张卡片只解释当前区域；顶部说明允许更详细；按钮只用动作动词；避免“多线程领奖”这类误导词。

## Implementation constraints
- Framework/styling system: Python Tkinter + ttk，不新增 UI 依赖。
- Design-token constraints: 自绘控件和颜色在 `bili_drop_guard/gui.py` 内维护。
- Performance constraints: UI 改动不得影响 watcher、Cookie 捕获、日志队列、配置保存和 PyInstaller 打包。
- Compatibility constraints: 保持 Windows、Edge/Chrome、Selenium、PyInstaller 当前路径。
- Test/screenshot expectations: UI 改动后运行 `python -m unittest discover -s tests -v` 和 `py_compile`；可运行时截图检查默认窗口是否无裁切、无重叠、无系统硬控件突兀。

## Open questions

- 暂无。v0.4.0 已实现”每个后台观看线程状态列表”（右栏后台计时状态卡）和”首次使用引导”（顶部”看上手指引”链接 + Toplevel modal）。
