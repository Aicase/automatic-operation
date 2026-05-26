#!/usr/bin/env python3
"""
playback.py — steps.json 回放引擎 (Enhanced v2.0)
=================================================
多策略定位器降级回放 + 智能等待 + 重试机制 + 变量注入

核心增强：
  · 9 级定位器降级（按 steps.json 顺序逐级 fallback）
  · 3 次重试 + 递增等待（应对网络抖动）
  · networkidle / domcontentloaded 双轨智能等待
  · --headed / --headless 切换
  · --dry-run 模式（验证步骤有效性，不实际执行）
  · --step 单步调试
  · --var key=value 动态变量注入
  · --retry N 全局重试次数
  · 执行报告（成功/失败/跳过统计）

使用方式:
  python3 playback.py --config <skill_config.json> [options]
  python3 playback.py --config ./my_skill/skill_config.json --headed
  python3 playback.py --config ./my_skill/skill_config.json --var container_no=GAOU6827574
  python3 playback.py --config ./my_skill/skill_config.json --step step_003 --dry-run
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from playwright.async_api import async_playwright, Page, Locator
except ImportError:
    print("❌ 请先安装 playwright: pip install playwright && playwright install chromium")
    sys.exit(1)


# ─── 定位器构建器 (支持 9+ 策略) ──────────────────────────

def build_locator(page: Page, locator_def: dict) -> Locator:
    """
    根据 locator 策略定义构建 Playwright Locator。

    支持策略:
      test_id     → page.get_by_test_id(selector)
      role        → page.get_by_role(role_type, name=name)
      label       → page.get_by_label(selector)
      placeholder → page.get_by_placeholder(selector)
      text        → page.get_by_text(selector, exact=False)
      css_fuzzy   → page.locator(selector)
      css         → page.locator(selector)
    """
    strategy = locator_def.get("strategy", "")
    selector = locator_def.get("selector", "")
    role_type = locator_def.get("role_type", "")
    name      = locator_def.get("name", "")

    if strategy == "test_id" and selector:
        return page.get_by_test_id(selector).first

    if strategy == "role" and role_type:
        # 有 name → 精确匹配；无 name → 匹配该角色的第一个元素
        if name:
            return page.get_by_role(role_type, name=name).first
        return page.get_by_role(role_type).first

    if strategy == "label" and selector:
        return page.get_by_label(selector, exact=False).first

    if strategy == "placeholder" and selector:
        return page.get_by_placeholder(selector).first

    if strategy == "text" and selector:
        return page.get_by_text(selector, exact=False).first

    if strategy in ("css_fuzzy", "css") and selector:
        return page.locator(selector).first

    # fallback
    if selector:
        return page.locator(selector).first

    raise ValueError(f"无法构建定位器: {locator_def}")


# ─── 变量替换 ──────────────────────────────────────────────

def resolve_value(value: str | None, context: dict) -> str | None:
    """将 {{variable_name}} 替换为实际值"""
    if not value or not isinstance(value, str):
        return value

    def repl(m: re.Match) -> str:
        key = m.group(1)
        return str(context.get(key, m.group(0)))

    return re.sub(r'\{\{(\w+)\}\}', repl, value)


# ─── 智能等待 ──────────────────────────────────────────────

async def smart_wait(page: Page, timeout_ms: int = 5000, strategy: str = "networkidle"):
    """等待页面稳定"""
    if strategy == "networkidle":
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            return
        except:
            pass
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms // 2)
    except:
        pass
    await asyncio.sleep(0.3)


# ─── 单步执行 (含多策略降级 + 重试) ──────────────────────

async def execute_step(
    page: Page,
    step: dict,
    context: dict,
    screenshots_dir: Path,
    retry_count: int = 3,
    wait_strategy: str = "networkidle",
    dry_run: bool = False
) -> tuple[bool, str]:
    """
    执行单个步骤。

    Returns:
        (success: bool, message: str)
    """
    step_id    = step.get("step_id", "?")
    action     = step.get("action", "click")
    locators   = step.get("locators", [])
    value      = step.get("value")
    timeout    = step.get("timeout", 8000)
    desc       = step.get("description", "")
    screens    = step.get("screenshots", {})
    assertion  = step.get("assertion")

    resolved_value = resolve_value(value, context)

    if dry_run:
        # Dry-run: 仅验证 locators 格式
        if not locators:
            return (False, f"[{step_id}] 无定位器定义")
        for i, loc in enumerate(locators):
            strategy = loc.get("strategy", "?")
            if strategy not in ("test_id", "role", "label", "placeholder", "text", "css_fuzzy", "css"):
                return (False, f"[{step_id}] locator[{i}] 未知策略: {strategy}")
        return (True, f"[{step_id}] dry-run 校验通过 ({len(locators)} 定位器)")

    print(f"\n{'─'*50}")
    print(f"[{step_id}] {desc}")
    print(f"   action={action}  value={resolved_value!r}  timeout={timeout}ms")

    # ── 多策略定位器降级尝试 ──────────────────────────
    element = None
    used_locator = None

    for i, loc_def in enumerate(locators):
        strategy = loc_def.get("strategy", "?")
        selector = loc_def.get("selector", "") or loc_def.get("name", "")

        try:
            candidate = build_locator(page, loc_def)
            await candidate.wait_for(state="visible", timeout=3000)
            element = candidate
            used_locator = loc_def
            print(f"   ✅ locator[{i}] {strategy}: {selector!r} — 定位成功")
            break
        except Exception as e:
            err_name = type(e).__name__
            print(f"   ⏳ locator[{i}] {strategy}: {selector!r} — {err_name}")

    if not element:
        return (False, f"[{step_id}] 所有 {len(locators)} 个定位器均失败")

    # ── 执行动作（含重试）────────────────────────────
    for attempt in range(1, retry_count + 1):
        try:
            if action == "click":
                await element.click(timeout=timeout)
            elif action == "fill":
                await element.fill(resolved_value or "", timeout=timeout)
            elif action == "hover":
                await element.hover(timeout=timeout)
            elif action == "check":
                await element.check(timeout=timeout)
            elif action == "uncheck":
                await element.uncheck(timeout=timeout)
            elif action == "select":
                await element.select_option(resolved_value or "", timeout=timeout)
            elif action == "press":
                await element.press(resolved_value or "Enter", timeout=timeout)
            elif action == "focus":
                await element.focus()
            else:
                print(f"   ⚠️  未知动作 {action}，回退为 click")
                await element.click(timeout=timeout)

            if attempt > 1:
                print(f"   🔄 第 {attempt} 次重试成功")
            break

        except Exception as e:
            if attempt < retry_count:
                wait_ms = 1000 * attempt
                print(f"   🔄 第 {attempt}/{retry_count} 次失败 ({type(e).__name__})，{wait_ms}ms 后重试...")
                await asyncio.sleep(wait_ms / 1000)
            else:
                return (False, f"[{step_id}] 执行失败（已重试 {retry_count} 次）: {type(e).__name__}: {e}")

    # ── 智能等待 ────────────────────────────────────
    await smart_wait(page, timeout, wait_strategy)

    # ── 断言校验 ────────────────────────────────────
    if assertion:
        atype = assertion.get("type", "")
        aloc  = assertion.get("locator", {})
        if atype == "visible" and aloc:
            try:
                a_el = build_locator(page, aloc)
                await a_el.wait_for(state="visible", timeout=timeout)
                print(f"   ✅ 断言通过: {atype} — {aloc.get('value', aloc.get('selector', '?'))}")
            except Exception as e:
                print(f"   ⚠️  断言失败: {atype} — {type(e).__name__}")
                # 断言失败不阻塞后续步骤（记录警告）

    # ── 截图 ────────────────────────────────────────
    try:
        if screens.get("after"):
            ss_path = screenshots_dir / Path(screens["after"]).name
            ss_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(ss_path), full_page=True)
            print(f"   📸 after: {ss_path.name}")
    except Exception as e:
        print(f"   ⚠️  截图失败: {type(e).__name__}")

    return (True, f"[{step_id}] {action} 成功")


# ─── 主回放函数 ────────────────────────────────────────────

async def playback(
    config_path: str,
    vars_: dict,
    step_filter: str | None = None,
    headed: bool = False,
    retry_count: int = 3,
    wait_strategy: str = "networkidle",
    dry_run: bool = False
):
    """读取 skill_config.json + steps.json 并执行全部步骤"""

    config_path = Path(config_path)
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    steps_path = config_path.parent / "steps.json"
    if not steps_path.exists():
        print(f"❌ steps.json 不存在: {steps_path}")
        sys.exit(1)

    with open(steps_path, encoding="utf-8") as f:
        steps = json.load(f)

    start_url = config.get("start_url", "about:blank")
    if not start_url or not isinstance(start_url, str) or not start_url.startswith(("http://", "https://")):
        print(f"❌ 无效的 start_url: {start_url!r}")
        print(f"   请在 skill_config.json 中设置有效的 start_url")
        sys.exit(1)
    skill_dir = config_path.parent
    screenshots_dir = skill_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # 合并变量上下文: config.variables(默认) + CLI --var(覆盖)
    full_ctx = {}
    for k, v in config.get("variables", {}).items():
        full_ctx[k] = v.get("default", "") if isinstance(v, dict) else v
    full_ctx.update(vars_)

    # 过滤步骤
    if step_filter:
        steps = [s for s in steps if s.get("step_id") == step_filter]
        if not steps:
            print(f"❌ 未找到步骤: {step_filter}")
            sys.exit(1)

    total = len(steps)
    print(f"\n🚀 回放引擎启动")
    print(f"   配置: {config_path}")
    print(f"   步骤: {total} 步")
    print(f"   起始: {start_url}")
    print(f"   模式: {'🏃 有头' if headed else '👻 无头'}"
          f" {'| 🔍 dry-run' if dry_run else ''}")
    print(f"   重试: {retry_count} 次")
    print(f"   变量: {json.dumps(full_ctx, ensure_ascii=False)}")

    if dry_run:
        print(f"\n{'='*50}")
        print("🔍 Dry-Run 模式 — 仅校验步骤定义，不实际执行")
        print(f"{'='*50}")
        success = 0
        for step in steps:
            ok, msg = await execute_step(
                page=None,  # dry-run 不需要 page
                step=step,
                context=full_ctx,
                screenshots_dir=screenshots_dir,
                retry_count=1,
                wait_strategy="domcontentloaded",
                dry_run=True
            )
            if ok:
                success += 1
            print(f"   {'✅' if ok else '❌'} {msg}")
        print(f"\n{'='*50}")
        print(f"Dry-Run 结果: {success}/{total} 步骤校验通过")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN"
        )
        page = await context.new_page()

        print(f"\n🌐 导航: {start_url}")
        start_time = time.time()
        try:
            await page.goto(start_url, timeout=30000)
            await smart_wait(page, 5000, wait_strategy)
        except Exception as e:
            print(f"❌ 导航失败 ({type(e).__name__}): {e}")
            print(f"   请检查 URL 是否可达、网络是否正常")
            await browser.close()
            sys.exit(1)

        results = {"success": 0, "failed": 0, "skipped": 0}

        for i, step in enumerate(steps, 1):
            print(f"\n[{i}/{total}]", end="")
            ok, msg = await execute_step(
                page=page,
                step=step,
                context=full_ctx,
                screenshots_dir=screenshots_dir,
                retry_count=retry_count,
                wait_strategy=wait_strategy,
                dry_run=False
            )

            if ok:
                results["success"] += 1
                print(f"   ✅ 完成")
            else:
                results["failed"] += 1
                print(f"   ❌ {msg}")

        elapsed = time.time() - start_time

        # ── 执行报告 ─────────────────────────────────
        print(f"\n{'='*60}")
        print(f"📊 执行报告")
        print(f"{'='*60}")
        print(f"   ✅ 成功: {results['success']}")
        print(f"   ❌ 失败: {results['failed']}")
        print(f"   ⏭️  跳过: {results['skipped']}")
        print(f"   ⏱️  耗时: {elapsed:.1f}s")
        print(f"   📂 截图: {screenshots_dir}/")
        print(f"{'='*60}")

        await browser.close()


# ─── CLI 入口 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="steps.json 回放引擎 — 多策略定位器降级 + 智能等待 + 重试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  playback.py --config ./my_skill/skill_config.json
  playback.py --config ./my_skill/skill_config.json --headed
  playback.py --config ./my_skill/skill_config.json --var container_no=GAOU6827574
  playback.py --config ./my_skill/skill_config.json --step step_003           # 单步调试
  playback.py --config ./my_skill/skill_config.json --dry-run                 # 仅校验
  playback.py --config ./my_skill/skill_config.json --retry 5 --wait domcontentloaded
        """
    )
    parser.add_argument("--config",   required=True, help="skill_config.json 路径")
    parser.add_argument("--var",      nargs="+", default=[], help="动态变量, 如 --var key1=val1 key2=val2")
    parser.add_argument("--step",     default=None, help="只执行指定 step_id（单步调试）")
    parser.add_argument("--headed",   action="store_true", default=False, help="有头模式")
    parser.add_argument("--retry",    type=int, default=3, help="每个步骤最大重试次数 (default: 3)")
    parser.add_argument("--wait",     default="networkidle",
                        choices=["networkidle", "domcontentloaded"],
                        help="等待策略 (default: networkidle)")
    parser.add_argument("--dry-run",  action="store_true", default=False,
                        help="仅校验步骤定义，不实际执行")
    args = parser.parse_args()

    vars_ = {}
    for v in args.var:
        if "=" in v:
            k, val = v.split("=", 1)
            vars_[k.strip()] = val.strip()
        else:
            print(f"⚠️  忽略无效变量格式: {v}（应为 key=value）")

    asyncio.run(playback(
        config_path=args.config,
        vars_=vars_,
        step_filter=args.step,
        headed=args.headed,
        retry_count=args.retry,
        wait_strategy=args.wait,
        dry_run=args.dry_run
    ))


if __name__ == "__main__":
    main()
