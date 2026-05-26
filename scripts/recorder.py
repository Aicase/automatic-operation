#!/usr/bin/env python3
"""
OpenClawRecorder — 自动录制引擎 (Enhanced v2.0)
===============================================
前端监听 → 后端捕获 → AI 精炼 → 执行校验 → 持久化

核心增强：
  · 可配置 AI 编配端点（环境变量 OPENCLAW_API_URL / OPENCLAW_API_KEY）
  · 9 级降级启发式定位器生成器（无 AI 时仍能高质量定位）
  · 键盘动作录制（Enter / Tab / Escape / 方向键）
  · 智能变量占位符建议（检测到输入框自动提示变量名）
  · --headed / --headless 模式切换
  · 网络空闲等待 + DOM 稳定等待双保险
  · 前后截图对比（before/after 快照留存）

使用方式:
  python3 recorder.py --skill-name <name> --start-url <url> [--save-path ./skills] [--headed] [--ai-refine]
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ 请先安装 playwright: pip install playwright && playwright install chromium")
    sys.exit(1)


SCRIPT_DIR = Path(__file__).parent
SPY_FILE   = SCRIPT_DIR / "record_spy.js"

# ─── 可配置 AI 端点 ─────────────────────────────────────
AI_API_URL  = os.environ.get("OPENCLAW_API_URL", "http://localhost:19789/v1/responses")
AI_API_KEY  = os.environ.get("OPENCLAW_API_KEY", "")
AI_MODEL    = os.environ.get("OPENCLAW_MODEL", "openclaw/main")
AI_TIMEOUT  = int(os.environ.get("AI_TIMEOUT", "30"))


class OpenClawRecorder:
    def __init__(self, skill_name: str, save_path: str = "./skills", headed: bool = False):
        self.skill_name     = skill_name
        self.save_root      = Path(save_path)
        self.skill_dir      = self.save_root / skill_name
        self.screenshots_dir  = self.skill_dir / "screenshots"
        self.scripts_dir    = self.skill_dir / "scripts"
        self.step_counter   = 0
        self.steps          = []
        self.variables      = {}       # 收集到的动态变量
        self.headed         = headed
        self._unique_labels  = set()   # 避免重复 label 变量名

        # 创建标准目录结构
        for d in (self.screenshots_dir, self.scripts_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.skill_config_path = self.skill_dir / "skill_config.json"
        self.steps_path        = self.skill_dir / "steps.json"

    # ─── 工具方法 ──────────────────────────────────────────

    def _ss_name(self, prefix: str) -> str:
        """生成截图文件名"""
        return f"{self.step_counter:03d}_{prefix}.png"

    def _ss_path(self, prefix: str) -> Path:
        return self.screenshots_dir / self._ss_name(prefix)

    async def _take_screenshot(self, page, path: Path):
        await page.screenshot(path=str(path), full_page=True)

    def _load_spy(self) -> str:
        if not SPY_FILE.exists():
            raise FileNotFoundError(f"探针脚本不存在: {SPY_FILE}")
        return SPY_FILE.read_text(encoding="utf-8")

    def _unique_var_name(self, base: str) -> str:
        """生成唯一变量名: container_no → container_no_2 → container_no_3"""
        if base not in self._unique_labels:
            self._unique_labels.add(base)
            return base
        i = 2
        while f"{base}_{i}" in self._unique_labels:
            i += 1
        name = f"{base}_{i}"
        self._unique_labels.add(name)
        return name

    # ─── 智能等待 ──────────────────────────────────────────

    async def _smart_wait(self, page, timeout_ms: int = 5000):
        """等待网络空闲或 DOM 稳定"""
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms // 2)
            except:
                pass  # 静默超时
        # 额外等一帧让渲染完成
        await asyncio.sleep(0.3)

    # ─── 值输入检测与变量建议 ──────────────────────────────

    def _detect_value_and_variable(self, payload: dict) -> tuple[str | None, str | None, dict | None]:
        """
        分析元素是否需要输入值，并建议变量占位符名。

        Returns:
            (action, suggested_var_name, var_meta)
        """
        element_type = payload.get("elementType", "")
        role   = payload.get("role", "")
        tag    = payload.get("tagName", "")
        ph     = payload.get("placeholder", "")
        label  = payload.get("labelText", "")
        name   = payload.get("name", "")
        text   = payload.get("text", "")

        # 需要输入值的元素类型
        if element_type in ("text_input", "dropdown") or role in ("textbox", "combobox"):
            # 推断变量名（按优先级）
            var_name = None
            suggestions = []

            # 1. 从 placeholder 推断
            if ph:
                # "Enter Container No." → container_no
                var_hint = ph.lower().replace(" ", "_").replace(".", "")
                var_hint = re.sub(r'[^a-z0-9_]', '', var_hint)
                if var_hint and len(var_hint) > 2:
                    var_name = var_hint
                    suggestions.append(f"从 placeholder '{ph}' 推断")

            # 2. 从 label 文本推断
            if label and not var_name:
                var_hint = label.lower().replace(" ", "_").replace("：", "").replace(":", "")
                var_hint = re.sub(r'[^a-z0-9_]', '', var_hint)
                if var_hint and len(var_hint) > 2:
                    var_name = var_hint
                    suggestions.append(f"从 label '{label}' 推断")

            # 3. 从 name 属性推断
            if name and not var_name:
                var_hint = name.lower().replace("-", "_").replace(" ", "_")
                var_hint = re.sub(r'[^a-z0-9_]', '', var_hint)
                if var_hint and len(var_hint) > 2:
                    var_name = var_hint
                    suggestions.append(f"从 name='{name}' 推断")

            # 4. 通用回退
            if not var_name:
                var_name = f"input_value_{len(self.variables) + 1}"
                suggestions.append("通用变量名")

            # 去重
            final_name = self._unique_var_name(var_name)

            var_meta = {
                "type": "string",
                "description": (
                    f"{' / '.join(suggestions) if suggestions else '输入值'} "
                    f"(tag={tag}, placeholder={ph or '无'})"
                ),
                "required": True
            }

            print(f"   💡 建议变量占位符: {{{{ {final_name} }}}} ({var_meta['description']})")

            return ("fill" if element_type == "text_input" else "select", final_name, var_meta)

        return (payload.get("action", "click"), None, None)

    # ─── 9 级降级启发式定位器生成器 ─────────────────────────

    def _heuristic_locators(self, payload: dict) -> list:
        """
        无 AI 参与时，基于元素特征生成多策略定位器。
        按 Playwright 推荐的稳定性排序。
        """
        locators = []
        test_id  = payload.get("testId")
        role     = payload.get("role", "")
        tag      = payload.get("tagName", "")
        ph       = payload.get("placeholder")
        label    = payload.get("labelText")
        aria     = payload.get("ariaLabel")
        text     = payload.get("text", "")
        classes  = payload.get("className", "")
        id_      = payload.get("id")
        name_    = payload.get("name")
        href     = payload.get("href")
        is_dyn   = payload.get("isDynamicId", True)

        # L1: data-testid / data-cy / data-qa / 稳定 id
        if test_id:
            locators.append({"strategy": "test_id", "selector": test_id})

        # L2: accessible role + name (Playwright 推荐的最稳健策略)
        if role and role not in ("textbox", "combobox"):
            # 用 aria-label、label、text 作为 name
            role_name = aria or label or text or ph
            if role_name:
                # 截短避免太长匹配失败
                name_for_role = role_name[:60].strip()
                if name_for_role:
                    locators.append({
                        "strategy": "role",
                        "role_type": role,
                        "name": name_for_role
                    })

        # L3: bare role（无 name 约束，匹配该角色的第一个元素）
        if role and not any(l.get("strategy") == "role" for l in locators):
            if tag in ("button", "a", "select", "input", "textarea"):
                locators.append({
                    "strategy": "role",
                    "role_type": role
                    # 不传 name 字段→匹配该 role 的第一个元素
                })

        # L4: label text（关联的 <label> 文本）
        if label:
            locators.append({"strategy": "label", "selector": label[:80]})

        # L5: placeholder（输入框首选）
        if ph:
            locators.append({"strategy": "placeholder", "selector": ph})

        # L6: text（短文本直接匹配）
        if text and len(text) < 60:
            locators.append({"strategy": "text", "selector": text[:60]})

        # L7: name 属性
        if name_ and not is_dyn:
            locators.append({"strategy": "css_fuzzy", "selector": f"{tag}[name='{name_}']"})

        # L8: css_fuzzy class 前缀（剥离哈希后缀）
        if classes:
            # 取第一个稳定 class 做前缀匹配
            parts = classes.split()
            if parts:
                prefix = parts[0]
                # 确保前缀不是太短
                if len(prefix) >= 3:
                    locators.append({
                        "strategy": "css_fuzzy",
                        "selector": f"{tag}[class^='{prefix}']"
                    })
                    # 再加一个包含匹配
                    if len(prefix) >= 5:
                        locators.append({
                            "strategy": "css_fuzzy",
                            "selector": f"{tag}[class*='{prefix}']"
                        })

        # L9: href（链接触底匹配）
        if href and not any(l.get("strategy") == "css_fuzzy" and "href" in l.get("selector", "") for l in locators):
            # 取路径部分
            if '/' in href and not href.startswith('javascript'):
                href_short = href.split('?')[0]
                if len(href_short) > 1:
                    locators.append({
                        "strategy": "css_fuzzy",
                        "selector": f"a[href*='{href_short}']"
                    })

        # 至少有一个定位器
        if not locators:
            # 最终兜底：tag + role 组合
            locators.append({
                "strategy": "css_fuzzy",
                "selector": f"{tag}" if not role else f"{tag}[role='{role}']"
            })

        print(f"   🔧 启发式生成 {len(locators)} 个定位策略")
        for i, l in enumerate(locators):
            s = l.get("strategy", "?")
            v = l.get("selector") or l.get("name") or l.get("role_type", "?")
            print(f"      L{i+1}: {s} = {v!r}")

        return locators

    # ─── AI 精炼定位器 ─────────────────────────────────────

    async def _ai_refine_locators(self, payload: dict) -> list:
        """调用 AI Agent 生成更精准的多策略定位器"""
        try:
            import requests

            prompt = f"""你是一个专门为自动化录制系统设计的"网页元素定位器生成专家"。

根据以下元素特征，生成按稳定性排序的 Playwright 多策略定位器数组。

元素数据:
{json.dumps(payload, ensure_ascii=False, indent=2)}

# 严格规则
1. 严禁使用动态哈希类名（如 Button__btn___2A8z9, css-175oi2r, sc-bdVaJa）
2. 按优先级排列: test_id → role → label → placeholder → text → css_fuzzy
3. css_fuzzy 必须使用 class^= 前缀匹配或 class*= 包含匹配，剥离随机后缀
4. 只返回纯 JSON 数组，不要 Markdown 代码块，不要解释

# 输出格式
[{{"strategy":"test_id|role|label|placeholder|text|css_fuzzy","selector":"...","role_type":"...","name":"..."}}]"""

            resp = requests.post(
                AI_API_URL,
                headers={
                    "Authorization": f"Bearer {AI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": AI_MODEL,
                    "input": prompt
                },
                timeout=AI_TIMEOUT,
            )

            if resp.status_code == 200:
                data = resp.json()
                text = data.get("output", [{}])[0].get("content", [{}])[0].get("text", "")
                m = re.search(r'\[[\s\S]*\]', text)
                if m:
                    locators = json.loads(m.group())
                    print(f"   🤖 AI 生成 {len(locators)} 个定位策略")
                    return locators
                else:
                    print(f"   ⚠️  AI 返回格式异常，未找到 JSON 数组")
            else:
                print(f"   ⚠️  AI 端点返回 {resp.status_code}，降级为启发式规则")

        except Exception as e:
            print(f"   ⚠️  AI 调用失败 ({type(e).__name__}: {e})，降级为启发式规则")

        return []  # 返回空列表表示 AI 失败，让调用方降级

    # ─── 录制主循环 ─────────────────────────────────────────

    async def start_record(self, start_url: str, use_ai_refine: bool = False):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not self.headed)
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="zh-CN"
            )
            page = await context.new_page()

            # ── Python 端点击数据接收管道 ─────────────────
            async def on_element_captured(data: dict):
                tag   = data.get("tagName", "?")
                role  = data.get("role", "?")
                text  = (data.get("text") or "")[:40]
                etype = data.get("elementType", "?")

                self.step_counter += 1
                print(f"\n{'─'*50}")
                print(f"[🟢 Step {self.step_counter}] <{tag}> role={role!r} type={etype}")

                # 拍摄动作前快照
                before_path = self._ss_path("before")
                await self._take_screenshot(page, before_path)
                print(f"   📸 before: {before_path.name}")

                # 检测是否需要输入值 + 建议变量名
                action, var_name, var_meta = self._detect_value_and_variable(data)

                # 用户手动确认变量名
                value = None
                if var_name:
                    prompt_text = (
                        f"   📝 检测到 {etype} 元素 (placeholder={data.get('placeholder')!r})\n"
                        f"      变量占位符: {{{{{var_name}}}}}\n"
                        f"      直接回车确认 / 输入新变量名 / 输入固定值 / 输入 'skip' 跳过: "
                    )
                    user_input = input(prompt_text).strip()
                    if user_input.lower() == 'skip':
                        value = None
                        action = "click"  # 跳过输入，只记录点击
                    elif user_input.startswith('{{') and user_input.endswith('}}'):
                        # 用户自定义变量名
                        custom_var = user_input[2:-2].strip()
                        var_name = self._unique_var_name(custom_var)
                        value = f"{{{{{var_name}}}}}"
                    elif user_input:
                        # 检查是否是一个简单的变量名（不含空格、非数字）
                        if user_input.isidentifier() and not user_input.isdigit():
                            var_name = self._unique_var_name(user_input)
                            value = f"{{{{{var_name}}}}}"
                        else:
                            # 固定值
                            value = user_input
                            var_name = None
                    else:
                        # 回车：使用默认变量名
                        value = f"{{{{{var_name}}}}}"

                    if var_name and var_meta:
                        self.variables[var_name] = var_meta
                else:
                    # 非输入元素，询问是否需要确认
                    confirm = input(f"   🔘 动作: {action} — 回车确认 / 输入 'skip' 跳过: ").strip()
                    if confirm.lower() == 'skip':
                        print(f"   ⏭️  已跳过")
                        return

                # 生成定位器（优先 AI 精炼，否则启发式）
                if use_ai_refine:
                    locators = await self._ai_refine_locators(data)
                    if not locators:
                        locators = self._heuristic_locators(data)
                else:
                    locators = self._heuristic_locators(data)

                # 在浏览器中执行动作（模拟）
                print(f"   🎬 执行动作: {action} value={value!r}")
                try:
                    if action == "click" and locators:
                        first_loc = locators[0]
                        if first_loc.get("strategy") == "test_id":
                            await page.get_by_test_id(first_loc["selector"]).first.click(timeout=5000)
                        elif first_loc.get("strategy") == "role":
                            role_name = first_loc.get("name") or None
                            await page.get_by_role(
                                first_loc["role_type"],
                                name=role_name
                            ).first.click(timeout=5000)
                        elif first_loc.get("strategy") == "text":
                            await page.get_by_text(first_loc["selector"], exact=False).first.click(timeout=5000)
                        else:
                            await page.locator(first_loc.get("selector", data.get("tagName", "div"))).first.click(timeout=5000)
                    elif action == "fill" and locators:
                        fill_val = value.replace("{{", "").replace("}}", "") if value else data.get("placeholder", "")
                        first_loc = locators[0]
                        await page.locator(first_loc.get("selector", "input")).first.fill(fill_val, timeout=5000)
                    elif action == "select" and locators:
                        # 对于 select，确保点击触发下拉
                        first_loc = locators[0]
                        await page.locator(first_loc.get("selector", "select")).first.click(timeout=5000)

                    print(f"   ✅ 动作执行成功")
                except Exception as e:
                    print(f"   ⚠️  动作模拟失败（不影响录制）: {type(e).__name__}: {e}")

                # 智能等待
                await self._smart_wait(page)

                # 拍摄动作后快照
                after_path = self._ss_path("after")
                await self._take_screenshot(page, after_path)

                # 构建 step 对象
                step = {
                    "step_id":     f"step_{self.step_counter:03d}",
                    "description": self._build_description(data, action, value),
                    "action":      action,
                    "locators":    locators,
                    "value":       value,
                    "timeout":     8000,
                    "screenshots": {
                        "before": f"screenshots/{before_path.name}",
                        "after":  f"screenshots/{after_path.name}"
                    }
                }

                # 添加断言（如果元素包含特定 class 或 result 区域）
                assertion = self._infer_assertion(data)
                if assertion:
                    step["assertion"] = assertion

                self.steps.append(step)

                # 实时写入 steps.json
                self._flush_steps()
                print(f"   📋 Step {self.step_counter} 已持久化")
                print(f"   📸 after:  {after_path.name}")
                print(f"{'─'*50}")

            # ── Python 端键盘数据接收管道 ─────────────────
            async def on_key_captured(data: dict):
                tag     = data.get("tagName", "?")
                key     = data.get("keyPressed", "?")
                etype   = data.get("elementType", "?")

                self.step_counter += 1
                print(f"\n{'─'*50}")
                print(f"[⌨️  Step {self.step_counter}] 按键 {key} 于 <{tag}> type={etype}")

                before_path = self._ss_path("before")
                await self._take_screenshot(page, before_path)

                # 按键动作使用当前元素的信息生成定位器
                locators = self._heuristic_locators(data)

                step = {
                    "step_id":     f"step_{self.step_counter:03d}",
                    "description": f"按下 {key} 键于 <{tag}> — {data.get('text', '')[:40]}",
                    "action":      "press",
                    "locators":    locators,
                    "value":       key,
                    "timeout":     5000,
                    "screenshots": {
                        "before": f"screenshots/{before_path.name}",
                        "after":  f"screenshots/{before_path.name}"  # 按键后暂时复用
                    }
                }
                self.steps.append(step)
                self._flush_steps()

                # 模拟按键
                try:
                    await page.keyboard.press(key)
                except:
                    pass

                await self._smart_wait(page)
                after_path = self._ss_path("after")
                await self._take_screenshot(page, after_path)
                step["screenshots"]["after"] = f"screenshots/{after_path.name}"
                self._flush_steps()

                print(f"   ⌨️  按键 {key} 已记录")
                print(f"{'─'*50}")

            # 注册回调
            await page.expose_function("onElementCaptured", on_element_captured)
            await page.expose_function("onKeyCaptured", on_key_captured)

            # 注入探针
            spy_code = self._load_spy()
            await page.add_init_script(spy_code)

            # 导航
            print(f"\n🚀 正在导航: {start_url}")
            await page.goto(start_url)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1)

            # 初始快照
            init_path = self.screenshots_dir / "000_init.png"
            await self._take_screenshot(page, init_path)
            print(f"📸 初始快照: {init_path.name}")

            print(f"""
{'='*60}
  🎬 录制模式已启动
{'='*60}
  操作指引:
    · 鼠标悬停目标元素 → 蓝色虚线高亮
    · 点击元素 → 绿色闪烁确认 → 特征捕获 → 写入步骤
    · 输入框 → 自动弹出变量名建议
    · 键盘 Enter/Tab → 记录按键动作
    · 终端输入 q → 退出录制并保存
{'='*60}
""")

            # 维持事件循环，等待用户输入 q
            loop = asyncio.get_event_loop()
            while True:
                line = await loop.run_in_executor(None, input, "")
                if line.strip().lower() == "q":
                    print("\n👋 正在退出录制...")
                    break
                elif line.strip().lower() == "s":
                    # s = 手动快照
                    ss_path = self.screenshots_dir / f"manual_{datetime.now():%H%M%S}.png"
                    await self._take_screenshot(page, ss_path)
                    print(f"📸 手动快照: {ss_path.name}")
                elif line.strip().lower() == "p":
                    # p = 打印当前步骤
                    print(json.dumps(self.steps, ensure_ascii=False, indent=2))
                await asyncio.sleep(0.1)

            await browser.close()

        # ── 写入 skill_config.json ──────────────────────
        config = {
            "skill_name":        self.skill_name,
            "version":           "1.0.0",
            "description":       f"自动录制的网页操作技能: {self.skill_name}",
            "start_url":         start_url,
            "variables":         self.variables,
            "recorded_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_steps":       self.step_counter,
            "recording_config":  {
                "headed":        self.headed,
                "ai_refined":    use_ai_refine,
            }
        }
        with open(self.skill_config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        print(f"\n{'='*60}")
        print(f"✅ 录制完成！")
        print(f"   📄 配置: {self.skill_config_path}")
        print(f"   📋 步骤: {self.steps_path} (共 {self.step_counter} 步)")
        print(f"   📸 截图: {self.screenshots_dir}/")
        print(f"   🔢 变量: {json.dumps(self.variables, ensure_ascii=False)}")
        print(f"\n   回放命令:")
        print(f"   python3 playback.py --config {self.skill_config_path}")
        print(f"   python3 generate_run.py --config {self.skill_config_path} --output {self.skill_dir}/run.py")
        print(f"{'='*60}")

    def _build_description(self, data: dict, action: str, value: str | None) -> str:
        """构建人类可读的步骤描述"""
        tag  = data.get("tagName", "?")
        text = data.get("text", "")[:40]
        ph   = data.get("placeholder", "")
        label = data.get("labelText", "")

        action_map = {
            "fill":   "填入",
            "click":  "点击",
            "select": "选择",
            "check":  "勾选",
            "press":  "按下",
            "hover":  "悬停",
        }
        act_cn = action_map.get(action, action)

        if action in ("fill", "select"):
            val_display = f"「{value}」" if value else ""
            if ph:
                return f"在 {ph!r} 输入框{act_cn}{val_display}"
            if label:
                return f"在「{label}」字段{act_cn}{val_display}"
            return f"在 <{tag}> 元素{act_cn}{val_display}"

        if action == "click":
            if text:
                return f"点击「{text}」"
            if label:
                return f"点击「{label}」按钮"
            return f"点击 <{tag}> 元素"

        if action == "press":
            return f"按下 {value} 键于 <{tag}>"

        return f"{act_cn} <{tag}> — {text}"

    def _infer_assertion(self, data: dict) -> dict | None:
        """根据元素特征推断断言条件"""
        etype = data.get("elementType", "")
        role  = data.get("role", "")
        tag   = data.get("tagName", "")

        # 提交按钮通常触发页面变化，添加等待断言
        if etype == "action_button" and (role == "button" or tag == "button"):
            # 尝试等待下一个可能的容器出现
            return None  # 用户可在后续手动添加

        return None

    def _flush_steps(self):
        """实时写入 steps.json"""
        with open(self.steps_path, "w", encoding="utf-8") as f:
            json.dump(self.steps, f, ensure_ascii=False, indent=2)


# ─── CLI 入口 ───────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw 自动录制引擎 — 前端监听 → 后端捕获 → AI 精炼 → 持久化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 recorder.py --skill-name sitc_login --start-url https://example.com
  python3 recorder.py --skill-name my_flow --start-url https://example.com --headed --ai-refine
  python3 recorder.py --skill-name my_flow --start-url https://example.com --save-path /path/to/skills
        """
    )
    parser.add_argument("--skill-name", required=True, help="技能名称（目录名）")
    parser.add_argument("--save-path",  default="./skills", help="技能根目录 (default: ./skills)")
    parser.add_argument("--start-url",  default=None, help="起始 URL")
    parser.add_argument("--headed",     action="store_true", default=False, help="有头模式（默认无头，用户根据截图描述操作）")
    parser.add_argument("--ai-refine",  action="store_true", default=False,
                        help="启用 AI 精炼定位器（需配置 OPENCLAW_API_URL / OPENCLAW_API_KEY）")
    args = parser.parse_args()

    url = args.start_url
    if not url:
        url = input("请输入目标网址: ").strip()
        if not url:
            print("❌ 未提供 URL，退出")
            sys.exit(1)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    recorder = OpenClawRecorder(
        skill_name=args.skill_name,
        save_path=args.save_path,
        headed=args.headed
    )
    asyncio.run(recorder.start_record(url, use_ai_refine=args.ai_refine))


if __name__ == "__main__":
    main()
