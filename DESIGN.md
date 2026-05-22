# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-05-22
- Primary product surfaces: Tkinter 桌面主窗口、Cookie 获取流程、直播观看配置、任务检查与领奖日志。
- Evidence reviewed: `bili_drop_guard/gui.py`、`bili_drop_guard/config.py`、`README.md`、`tests/`。

## Brand
- Personality: 专业、清晰、稳妥，像一个本地自动化控制台，而不是营销页。
- Trust signals: 明确显示当前状态、关键操作有上下文说明、日志持续可见、设置项命名与业务行为一致。
- Avoid: 花哨渐变、过度装饰、含糊按钮文案、把多窗口观看误导成多线程领奖。

## Product goals
- Goals: 让用户快速完成 Cookie 获取、填写直播间、设置观看窗口数、启动计时、查看任务/领奖结果。
- Non-goals: 不做账号托管、不内置账号、不承诺绕过平台规则、不隐藏真实浏览器行为。
- Success signals: 用户能一眼知道下一步做什么；Cookie 获取入口明显；观看窗口数和领奖线程的区别清楚；日志能解释失败原因。

## Personas and jobs
- Primary personas: 个人开发者/产品经理，熟悉产品目标但不希望理解复杂代码细节。
- User jobs: 启动本地工具、登录 B 站、用多个直播窗口累计观看时长、在条件满足时领取奖励。
- Key contexts of use: Windows 桌面、Edge/Chrome、本地调试和打包 exe 使用。

## Information architecture
- Primary navigation: 单页控制台，无多级导航。
- Core routes/screens: 主窗口分为顶部状态、左侧配置流程、右侧运行日志。
- Content hierarchy: 状态与主操作优先；Cookie 和直播配置在左侧按步骤排列；日志常驻右侧。

## Design principles
- Principle 1: 业务流程优先，按“获取 Cookie -> 设置直播 -> 开始观看 -> 领取奖励”的顺序组织。
- Principle 2: 所有高风险或容易误解的设置都给短说明，说明靠近控件。
- Tradeoffs: 保持 Tkinter 原生实现和轻量打包，视觉 polish 通过间距、分组、颜色和文案完成，不引入新依赖。

## Visual language
- Color: 浅灰应用背景、白色配置面板、深色日志区、蓝色主操作、红色停止操作。
- Typography: Windows 上优先使用 `Microsoft YaHei UI`，标题中等大小，工具面板内避免夸张字号。
- Spacing/layout rhythm: 使用 8/12/16/24 的间距节奏，控件边缘对齐，主要区域保持稳定比例。
- Shape/radius/elevation: Tkinter 内使用细边框和浅色面板表达层级，不依赖重阴影。
- Motion: 不使用装饰动画；状态变化通过状态标签和日志反馈。
- Imagery/iconography: 不引入额外图片资产；按钮以清晰动词命名。

## Components
- Existing components to reuse: `ttk.Frame`、`ttk.Label`、`ttk.Button`、`ttk.Entry`、`ttk.Spinbox`、`ttk.Checkbutton`、`tk.Text`。
- New/changed components: 步骤条、卡片式配置区、状态徽标、日志说明区、统一按钮栏。
- Variants and states: 主按钮、次按钮、危险按钮、状态标签、提示文字、文本输入区、日志区。
- Token/component ownership: 颜色、字体、间距和按钮样式集中在 `App._configure_style()`。

## Accessibility
- Target standard: 面向桌面工具的基础可读性和键盘可用性。
- Keyboard/focus behavior: 输入框、按钮、勾选框保留 Tkinter 默认键盘焦点行为。
- Contrast/readability: 深色日志区使用高对比文字；提示文字使用中性灰但不低于可读阈值。
- Screen-reader semantics: 使用真实控件和文本标签，不用图片替代关键信息。
- Reduced motion and sensory considerations: 无自动动画和闪烁效果。

## Responsive behavior
- Supported breakpoints/devices: Windows 桌面窗口，最小宽度约 980px。
- Layout adaptations: 主窗口宽屏为左右两栏；最小尺寸下保持表单和日志可见。
- Touch/hover differences: 面向鼠标键盘，不依赖 hover 才能理解功能。

## Interaction states
- Loading: Cookie 获取时状态标签显示“正在获取 Cookie”，日志同步提示。
- Empty: Cookie、任务 ID 可为空；任务 ID 空值代表自动识别。
- Error: 后台错误进入日志并弹窗提示关键失败。
- Success: Cookie 获取成功后自动回填并保存；启动/停止/领奖进入日志。
- Disabled: 当前版本不禁用按钮，重复操作由现有逻辑拦截并写日志。
- Offline/slow network, if applicable: 通过日志显示接口失败、登录超时或浏览器连接失败。

## Content voice
- Tone: 直接、务实、少术语。
- Terminology: 使用“直播窗口数”表示并行观看窗口；使用“领奖”表示单线程提交领取请求。
- Microcopy rules: 按钮使用动作动词；提示说明只保留用户下一步需要知道的信息。

## Implementation constraints
- Framework/styling system: Python Tkinter + ttk，不新增 UI 依赖。
- Design-token constraints: 颜色和字体写在 `bili_drop_guard/gui.py` 的样式配置内。
- Performance constraints: UI 重排不得影响 watcher、Cookie 捕获、日志队列和保存配置逻辑。
- Compatibility constraints: 保持 Windows、PyInstaller、Edge/Chrome 自动化路径。
- Test/screenshot expectations: 修改后运行单元测试；可运行应用时进行启动冒烟检查。

## Open questions
- [ ] 是否需要后续加入“当前观看窗口健康状态”列表 / 用户 / 影响：能更直观看到每个直播窗口是否真的计时。
- [ ] 是否需要在 UI 中单独解释“领奖固定 1 个线程” / 用户 / 影响：减少对多线程设置用途的误解。
