---
name: automatic-operation-skill
version: 2.0.0
description: |
  网页自动化操作录制 Skill（全栈标准规范 v2.0）。
  前端监听 → 后端捕获 → AI 精炼 → 执行校验，四维闭环确保定位精准。
  当用户说"帮我录一个XXX网站的自动化流程"时激活。
  一次录制，普遍适用——生成的脚本具备极强的抗动态干扰能力。
---

# Automatic Operation Skill v2.0

## 概述

本 Skill 将繁琐的「审查元素 → 寻找 CSS → 编写脚本」工作，完全转化为「在浏览器里正常操作，后端自动收网」的极致体验。

### 核心能力

| 能力 | 说明 |
|------|------|
| 🎬 **可视化录制** | 浏览器中操作，探针自动捕获多维特征 |
| 🧠 **AI 精炼定位器** | 9 级降级策略，自动过滤动态哈希类名 |
| 🔄 **多策略回放** | test_id → role → label → placeholder → text → css_fuzzy |
| 🛡️ **抗干扰设计** | 前序策略失败自动降级，杜绝样式小改版导致的脚本雪崩 |
| 📸 **溯源快照** | 每一步 before/after 截图留存 |
| 🔢 **参数化变量** | 输入值自动识别为 {{variable_name}} 占位符 |

---

## 项目结构

```
automatic-operation-skill/
├── skill_config.json    # 全局配置：入口 URL、全局变量与环境依赖
├── steps.json           # 核心资产：高容错、结构化的多策略操作步骤
├── SKILL.md             # 业务手册（本文档）
├── scripts/
│   ├── recorder.py      # 录制引擎：Playwright + 探针注入 → AI 精炼 → 持久化
│   ├── record_spy.js    # 前端探针：浏览器端多维特征捕获（支持 Shadow DOM）
│   ├── playback.py      # 回放引擎：多策略降级 + 重试 + 智能等待
│   ├── generate_run.py  # 脚本生成器：从 steps.json 生成独立 run.py
│   └── auto-operate.js  # [Legacy] playwright-cli 辅助脚本
└── screenshots/         # 溯源快照：000_init.png / 001_before.png / 001_after.png
```

---

## 闭环录制工作流

### Step 1：初始化环境与参数声明

用户输入技能名称和存放路径。系统创建标准目录结构，初始化 `skill_config.json`，以有头模式启动 Playwright。

```bash
python3 scripts/recorder.py --skill-name <name> --start-url <url> --headed
```

- 自动创建 `skill_config.json`（声明 start_url、variables）
- 启动 Chromium 浏览器（默认无头模式，用户**根据截图描述操作**；`--headed` 可切换到所见模式）
- 拍摄初始快照 `000_init.png`

### Step 2：注入捕获探针 (Spy Hook)

目标网页静止后，系统向浏览器上下文注入 `record_spy.js` 探针脚本。

探针能力：
- **鼠标悬停**：蓝色虚线高亮所有可选元素
- **点击拦截**：捕获阶段阻止默认跳转，提取多维特征
- **键盘监听**：Enter / Tab / Escape / 方向键自动记录
- **Shadow DOM 穿透**：支持 Web Components
- **Label 关联**：自动提取 `<label for="...">` 文本

### Step 3：多维特征捕获与对齐

用户操作元素时，探针秒级抓取：

| 特征 | 来源 | 用途 |
|------|------|------|
| `tagName` | 元素标签 | 角色推断 |
| `role` | 隐式无障碍角色 | Playwright role 定位 |
| `elementType` | 元素分类 | 动作推断 |
| `text` | Shadow DOM 穿透文本 | text 定位 |
| `labelText` | 关联 `<label>` 文本 | label 定位 |
| `testId` | data-testid / data-cy / data-qa / id | 最稳定定位 |
| `placeholder` | placeholder 属性 | placeholder 定位 |
| `className` | 过滤动态哈希后的稳定类名 | css_fuzzy 定位 |
| `htmlFragment` | 壳结构 HTML（≤250 字符） | AI 分析 |
| `boundingBox` | 元素坐标 + 尺寸 | 可见性校验 |
| `modifiers` | Shift / Ctrl 修饰键状态 | 组合操作 |

### Step 4：AI 精炼多策略定位器

Python 后端收到底层数据后，按以下路径生成多策略定位器：

**路径 A（默认）：9 级启发式降级规则**

```
L1: test_id      — data-testid / data-cy / data-qa / 稳定 id
L2: role + name  — 无障碍角色 + aria-label / label / text
L3: role         — 无障碍角色（不含 name，匹配第一个）
L4: label        — 关联 <label for="..."> 文本
L5: placeholder  — 输入框 placeholder 属性
L6: text         — 元素固定文本（≤60 字符）
L7: name         — name 属性（非动态 id）
L8: css_fuzzy    — class 前缀匹配 class^='prefix'
L9: href         — 链接触底匹配 a[href*='path']
```

**路径 B（可选）：AI 端点精炼**

```bash
# 配置环境变量
export OPENCLAW_API_URL="http://your-ai-endpoint/v1/responses"
export OPENCLAW_API_KEY="your-api-key"

# 启用 AI 精炼
python3 scripts/recorder.py --skill-name my_flow --start-url https://... --ai-refine
```

### Step 5：动作模拟、智能等待与持久化

- 系统在浏览器中模拟执行动作（验证定位器有效性）
- 自动触发网络空闲等待（networkidle）
- 拍摄 after 快照 ⇢ 步骤实时写入 `steps.json`
- 循环直到用户输入 `q` 退出

---

## 核心 Schema

### skill_config.json

```json
{
  "skill_name": "sitc_shipping_tracking",
  "version": "1.0.0",
  "description": "自动查询SITC船公司指定集装箱的最新物流状态",
  "start_url": "https://example-shipping.com/tracking",
  "variables": {
    "container_no": {
      "type": "string",
      "description": "提单号/集装箱号",
      "default": "",
      "required": true
    }
  },
  "recorded_at": "2026-05-20T12:00:00Z",
  "total_steps": 2,
  "recording_config": {
    "headed": true,
    "ai_refined": false
  }
}
```

### steps.json

每个步骤包含：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `step_id` | string | ✅ | 唯一标识 `step_001` |
| `description` | string | ✅ | 人类可读描述 |
| `action` | enum | ✅ | `click` / `fill` / `hover` / `press` / `check` / `uncheck` / `select` / `focus` |
| `locators` | array | ✅ | 多策略定位器（按稳定性降序） |
| `value` | string\|null | — | 输入值；`{{var}}` 表示动态变量 |
| `timeout` | number | — | 超时毫秒（默认 8000） |
| `assertion` | object | — | 验证条件 `{"type":"visible","locator":{...}}` |
| `screenshots` | object | — | `before` / `after` 截图路径 |

### locator 策略定义

```json
{
  "strategy": "test_id | role | label | placeholder | text | css_fuzzy | css",
  "selector": "定位选择器（非 role 策略）",
  "role_type": "button | textbox | link | combobox | checkbox（仅 role 策略）",
  "name": "无障碍名称或文本（仅 role 策略）"
}
```

---

## 使用方式

### 录制

```bash
# 基础录制（启发式定位器）
python3 scripts/recorder.py --skill-name my_flow --start-url https://example.com --headed

# AI 精炼录制
python3 scripts/recorder.py --skill-name my_flow --start-url https://example.com --headed --ai-refine

# 自定义保存路径
python3 scripts/recorder.py --skill-name my_flow --start-url https://example.com --save-path /path/to/skills
```

录制过程中：
- 鼠标悬停 → 蓝色虚线高亮
- 点击元素 → 绿色闪烁确认 → 自动弹出变量名建议（输入框时）
- 键盘 Enter/Tab → 记录按键步骤
- 终端输入 `q` → 退出并保存
- 终端输入 `s` → 手动快照
- 终端输入 `p` → 打印当前步骤

### 回放

```bash
# 基础回放
python3 scripts/playback.py --config ../skill_config.json

# 有头模式 + 变量注入
python3 scripts/playback.py --config ../skill_config.json --headed --var container_no=GAOU6827574

# 单步调试
python3 scripts/playback.py --config ../skill_config.json --step step_003 --headed

# 仅校验（不实际执行）
python3 scripts/playback.py --config ../skill_config.json --dry-run

# 重试 5 次 + DOM 等待
python3 scripts/playback.py --config ../skill_config.json --retry 5 --wait domcontentloaded
```

### 生成独立脚本

```bash
# 从 steps.json 生成独立部署脚本
python3 scripts/generate_run.py --config ../skill_config.json

# 指定输出路径
python3 scripts/generate_run.py --config ../skill_config.json --output /path/to/run.py

# 运行生成的脚本
python3 ../run.py --headed --var container_no=GAOU6827574
```

---

## AI 决策引擎提示词 (System Prompt)

> 以下提示词用于 AI 精炼模式（`--ai-refine`）。当 recorder.py 捕获到元素特征后，将其发送给 AI Agent 生成多策略定位器。

```
# Role
你是一个专门为 OpenClaw 自动化录制系统设计的"网页元素分析与鲁棒定位器（Robust Locator）生成专家"。
你的核心任务是将用户粗糙的单步操作意图和抓取到的局部 HTML 片段，提炼、翻译为符合 Playwright 最佳实践、
具备极高抗网页变动能力的结构化操作步骤。

# Absolute Rules & Constraints
1. 彻底摒弃脆弱的选择器：严禁直接使用包含长路径、绝对路径或带有动态哈希
   （如 Button__btn___2A8z9, css-175oi2r, sc-bdVaJa 等 React/Vue 打包生成的随机类名）的 CSS Selector。
2. 多策略降级（Ranked Locators）：对于每一个操作元素，你必须同时生成一个定位策略数组
   （由最稳健到相对稳健）。回放引擎将按顺序尝试，直到定位成功。
3. 参数化识别：如果用户的输入内容看起来像是一个动态变量（例如账号、密码、订单号、箱号），
   你必须将其转化为 {{variable_name}} 格式的变量占位符，并在说明中备注。
4. 输出格式：只允许输出一个纯粹的 JSON 数组，不得包含任何 Markdown 代码块外的解释性文字或前后寒暄。

# Locator Strategy Ranking (Priority Order)
当你分析 HTML 片段时，必须严格按照以下顺序提取和排列 locators：
1. test_id: 查找是否存在 data-testid, data-qa, data-cy, data-test, id（确认 id 不是动态生成的数字/随机串）。
2. role: 利用 Playwright 推荐的无障碍角色定位。结合 HTML 标签名或 role 属性，
   并匹配其可访问文本名称（aria-label, title, 内部文本 innerText）。
3. label: 如果存在关联的 <label for="..."> 文本，使用 label 策略。
4. placeholder: 如果是输入框，优先提取 placeholder 属性的值。
5. text: 提取元素内部唯一的、不易改变的固定文本（如"忘记密码"、"提交订单"）。
6. css_fuzzy: 如果以上策略均不完美，才允许使用 CSS 属性模糊匹配。
   例如针对 class="Button_btn__x921a"，应转化为模糊匹配：button[class^='Button_btn']（以该字符串开头）
   或 button[class*='_btn']（包含该字符串），从而彻底剔除末尾的随机哈希。

# Output JSON Schema
输出的 JSON 结构必须严格符合以下规范：
[
  {
    "strategy": "必须是 'test_id', 'role', 'label', 'placeholder', 'text', 'css_fuzzy' 之一",
    "role_type": "（仅当strategy为role时包含）例如 'button', 'textbox', 'link'",
    "name": "（仅当strategy为role时包含）无障碍名称或内部文本",
    "selector": "（非role策略时包含）具体的定位选择器或匹配值"
  }
]
```

---

## 录制前准备

### 环境依赖

```bash
# Python 3.10+
pip install playwright
playwright install chromium

# 可选：AI 精炼
pip install requests
```

### 目录准备

录制前确保目标路径可写。录制器会自动创建标准目录结构。

---

## 高级特性

### 变量注入

录制时输入框自动弹出变量名建议，可在 `skill_config.json` 中预定义别名：

```json
{
  "variables": {
    "container_no": {
      "type": "string",
      "description": "集装箱号",
      "default": "GAOU6827574",
      "required": true
    },
    "booking_ref": {
      "type": "string",
      "description": "订舱号",
      "required": false
    }
  }
}
```

回放时通过 CLI 注入或使用默认值：

```bash
python3 playback.py --config skill_config.json --var container_no=ABCD1234567
```

### 断言配置

在 `steps.json` 中手动添加 assertion，确保关键步骤后页面状态正确：

```json
{
  "step_id": "step_002",
  "action": "click",
  "locators": [...],
  "assertion": {
    "type": "visible",
    "locator": {
      "strategy": "css_fuzzy",
      "selector": ".tracking-result-table"
    }
  }
}
```

### Cookie / 登录态保持

录制完成后，可手动在 `steps.json` 首步前插入 cookie 注入步骤，或通过 Playwright 的 `storageState` 保存登录态。

---

## 常见问题

### Q: 探针没有捕获到点击？

1. 检查 `record_spy.js` 是否成功注入（打开浏览器 DevTools → Console 查看报错）
2. 确认目标网页未设置严格的 CSP（Content Security Policy）阻止内联脚本
3. 某些 SPA 框架（如 React Portal）渲染的元素可能不在常规 DOM 树中，尝试手动点击

### Q: 回放时元素找不到？

1. 使用 `--dry-run` 校验步骤定义
2. 使用 `--step step_xxx` 单步调试
3. 检查 `steps.json` 中 locators 数组，确保前几个策略覆盖当前页面的元素特征
4. 增大 `--retry` 次数（默认 3）
5. 切换 `--wait domcontentloaded` 等待策略

### Q: 生成的 run.py 无法运行？

1. 确保目标环境已安装 Playwright
2. 检查 `skill_config.json` 中 `start_url` 是否正确
3. 确认 `steps.json` 中的 locator 选择器在目标页面有效

### Q: 如何录制键盘操作（Tab切换/Enter提交）？

前端探针已自动监听 Enter / Tab / Escape / 方向键。
在输入框中按下这些键时，探针会自动捕获并记录为 `press` 动作。

### Q: 能否录制拖拽、滚动等操作？

当前版本支持：click, fill, hover, press, check, uncheck, select, focus。
拖拽（drag & drop）和滚动（scroll）需要手动在 `steps.json` 中添加自定义步骤。

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-05-20 | 初始版本：基础录制 + 回放 |
| 2.0.0 | 2026-05-22 | 全面增强：9 级启发式定位器、AI 精炼、键盘录制、独立脚本生成、重试机制、Shadow DOM 支持 |

---

_Generated by OpenClaw Automatic Operation Skill framework._
