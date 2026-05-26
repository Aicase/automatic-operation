#!/usr/bin/env python3
"""
generate_run.py — 独立脚本生成器
==================================
从 skill_config.json + steps.json 生成独立的 run.py Playwright 脚本。
生成的脚本无需依赖 playback.py，可直接部署运行。

使用方式:
  python3 generate_run.py --config <skill_config.json> --output <output.py>
  python3 generate_run.py --config ./my_skill/skill_config.json
  python3 generate_run.py --config ./my_skill/skill_config.json --output /path/to/run.py
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _json_to_py_literal(obj) -> str:
    """JSON dump 输出转为 Python 可用字面量（null→None, true→True, false→False）"""
    text = json.dumps(obj, ensure_ascii=False, indent=4)
    # 替换 JSON 语法为 Python 语法
    text = text.replace(": null", ": None")
    text = text.replace(": true", ": True")
    text = text.replace(": false", ": False")
    text = text.replace("null,", "None,")
    text = text.replace("null\n", "None\n")
    text = text.replace("true,", "True,")
    text = text.replace("false,", "False,")
    text = text.replace("true\n", "True\n")
    text = text.replace("false\n", "False\n")
    return text


def generate(config_path: str, output_path: str | None = None) -> str:
    """从 skill_config.json + steps.json 生成独立 run.py 脚本"""

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    steps_path = config_path.parent / "steps.json"
    if not steps_path.exists():
        raise FileNotFoundError(f"steps.json 不存在: {steps_path}")

    with open(steps_path, encoding="utf-8") as f:
        steps = json.load(f)

    skill_name = config.get("skill_name", "unknown_skill")
    skill_desc = config.get("description", f"自动化技能: {skill_name}")
    start_url  = config.get("start_url", "about:blank")

    # 提取默认变量值
    default_vars = {}
    for k, v in config.get("variables", {}).items():
        default_vars[k] = v.get("default", "") if isinstance(v, dict) else v

    # 如果不是绝对路径，确定输出路径
    if not output_path:
        output_path = config_path.parent / "run.py"
    else:
        output_path = Path(output_path)

    # 构建脚本内容
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version   = config.get("version", "1.0.0")

    lines = _build_script(
        skill_desc=skill_desc,
        timestamp=timestamp,
        source=f"{skill_name} (v{version})",
        script_name=output_path.name,
        start_url=start_url,
        default_variables=default_vars,
        steps=steps,
    )

    script = "\n".join(lines) + "\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(script)

    os.chmod(output_path, 0o755)

    print(f"✅ 独立脚本已生成: {output_path}")
    print(f"   📏 大小: {len(script)} 字符")
    print(f"   🎯 步骤: {len(steps)} 步")
    print(f"   🔢 变量: {len(default_vars)} 个")
    print(f"\n   运行方式:")
    print(f"   python3 {output_path}")
    if default_vars:
        vars_example = " ".join(f"--var {k}={v}" for k, v in list(default_vars.items())[:2])
        print(f"   python3 {output_path} {vars_example}")

    return str(output_path)


def _build_script(**ctx) -> list:
    """构建脚本的每一行，避免 .format() 转义地狱"""
    L = []  # lines accumulator

    def add(*lines):
        L.extend(lines)

    desc = ctx["skill_desc"]
    ts   = ctx["timestamp"]
    src  = ctx["source"]
    sn   = ctx["script_name"]
    url  = ctx["start_url"]
    dv   = _json_to_py_literal(ctx["default_variables"])
    st   = _json_to_py_literal(ctx["steps"])

    add(
        '#!/usr/bin/env python3',
        '"""',
        f'{desc}',
        '============================================================',
        f'自动生成脚本 | 创建时间: {ts}',
        f'来源: {src}',
        '',
        '运行方式:',
        f'  python3 {sn}                                    # 使用默认变量',
        f'  python3 {sn} --var container_no=GAOU6827574     # 注入变量',
        f'  python3 {sn} --headed                            # 有头模式',
        f'  python3 {sn} --step step_003                     # 单步执行',
        '"""',
        '',
        'import argparse',
        'import asyncio',
        'import json',
        'import re',
        'import sys',
        '',
        'try:',
        '    from playwright.async_api import async_playwright',
        'except ImportError:',
        '    print("请先安装 playwright: pip install playwright && playwright install chromium")',
        '    sys.exit(1)',
        '',
        '',
        '# ======================================================================',
        '# 技能配置（由生成器从 skill_config.json 注入）',
        '# ======================================================================',
        '',
        f'START_URL = {url!r}',
        f'DEFAULT_VARIABLES = {dv}',
        f'STEPS = {st}',
        '',
        '',
        '# ======================================================================',
        '# 定位器构建器',
        '# ======================================================================',
        '',
        'def build_locator(page, locator_def):',
        '    """根据 locator 策略构建 Playwright Locator"""',
        '    strategy = locator_def.get("strategy", "")',
        '    selector = locator_def.get("selector", "")',
        '    role_type = locator_def.get("role_type", "")',
        '    name      = locator_def.get("name", "")',
        '',
        '    if strategy == "test_id" and selector:',
        '        return page.get_by_test_id(selector).first',
        '    if strategy == "role" and role_type:',
        '        if name:',
        '            return page.get_by_role(role_type, name=name).first',
        '        return page.get_by_role(role_type).first',
        '    if strategy == "label" and selector:',
        '        return page.get_by_label(selector, exact=False).first',
        '    if strategy == "placeholder" and selector:',
        '        return page.get_by_placeholder(selector).first',
        '    if strategy == "text" and selector:',
        '        return page.get_by_text(selector, exact=False).first',
        '    if strategy in ("css_fuzzy", "css") and selector:',
        '        return page.locator(selector).first',
        '    if selector:',
        '        return page.locator(selector).first',
        '    raise ValueError("无法构建定位器: " + str(locator_def))',
        '',
        '',
        '# ======================================================================',
        '# 辅助函数',
        '# ======================================================================',
        '',
        'def resolve_value(value, context):',
        '    """替换 {{variable_name}} 占位符"""',
        '    if not value or not isinstance(value, str):',
        '        return value',
        '    def repl(m):',
        '        key = m.group(1)',
        '        return str(context.get(key, m.group(0)))',
        '    return re.sub(r"\\{\\{(\\w+)\\}\\}", repl, value)',
        '',
        '',
        'async def smart_wait(page, timeout_ms=5000):',
        '    """等待页面稳定"""',
        '    try:',
        '        await page.wait_for_load_state("networkidle", timeout=timeout_ms)',
        '    except Exception:',
        '        try:',
        '            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms // 2)',
        '        except Exception:',
        '            pass',
        '    await asyncio.sleep(0.3)',
        '',
        '',
        '# ======================================================================',
        '# 步骤执行器',
        '# ======================================================================',
        '',
        'async def execute_step(page, step, context, retry_count=3):',
        '    """执行单个步骤，多策略定位器降级 + 重试"""',
        '    step_id   = step.get("step_id", "?")',
        '    action    = step.get("action", "click")',
        '    locators  = step.get("locators", [])',
        '    value     = step.get("value")',
        '    timeout   = step.get("timeout", 8000)',
        '    desc      = step.get("description", "")',
        '',
        '    resolved_value = resolve_value(value, context)',
        '',
        '    print(f"\\n[{step_id}] {desc}")',
        '    print(f"   action={action}  value={resolved_value!r}")',
        '',
        '    # 多策略定位器降级',
        '    element = None',
        '    for i, loc_def in enumerate(locators):',
        '        strategy = loc_def.get("strategy", "?")',
        '        sel      = loc_def.get("selector", "") or loc_def.get("name", "")',
        '        try:',
        '            element = build_locator(page, loc_def)',
        '            await element.wait_for(state="visible", timeout=3000)',
        '            print(f"   OK locator[{i}] {strategy}: {sel!r}")',
        '            break',
        '        except Exception as e:',
        '            print(f"   .. locator[{i}] {strategy}: {sel!r} -- {type(e).__name__}")',
        '',
        '    if not element:',
        '        print(f"   FAIL 所有 {len(locators)} 个定位器均失败")',
        '        return False',
        '',
        '    # 执行动作（含重试）',
        '    for attempt in range(1, retry_count + 1):',
        '        try:',
        '            if action == "click":',
        '                await element.click(timeout=timeout)',
        '            elif action == "fill":',
        '                await element.fill(resolved_value or "", timeout=timeout)',
        '            elif action == "hover":',
        '                await element.hover(timeout=timeout)',
        '            elif action == "check":',
        '                await element.check(timeout=timeout)',
        '            elif action == "uncheck":',
        '                await element.uncheck(timeout=timeout)',
        '            elif action == "select":',
        '                await element.select_option(resolved_value or "", timeout=timeout)',
        '            elif action == "press":',
        '                await element.press(resolved_value or "Enter", timeout=timeout)',
        '            elif action == "focus":',
        '                await element.focus()',
        '            else:',
        '                await element.click(timeout=timeout)',
        '            break',
        '        except Exception as e:',
        '            if attempt < retry_count:',
        '                wait_ms = 1000 * attempt',
        '                print(f"   RETRY {attempt}/{retry_count} ({type(e).__name__})...")',
        '                await asyncio.sleep(wait_ms / 1000)',
        '            else:',
        '                print(f"   FAIL 执行失败: {e}")',
        '                return False',
        '',
        '    await smart_wait(page, timeout)',
        '',
        '    # 断言',
        '    assertion = step.get("assertion")',
        '    if assertion:',
        '        atype = assertion.get("type", "")',
        '        aloc  = assertion.get("locator", {})',
        '        if atype == "visible" and aloc:',
        '            try:',
        '                a_el = build_locator(page, aloc)',
        '                await a_el.wait_for(state="visible", timeout=timeout)',
        '                print(f"   OK 断言通过: {atype}")',
        '            except Exception:',
        '                print(f"   WARN 断言失败: {atype}")',
        '',
        '    print(f"   OK 完成")',
        '    return True',
        '',
        '',
        '# ======================================================================',
        '# 主流程',
        '# ======================================================================',
        '',
        'async def main(context, headed=False, step_filter=None):',
        '    async with async_playwright() as p:',
        '        browser = await p.chromium.launch(headless=not headed)',
        '        ctx = await browser.new_context(',
        '            viewport={"width": 1440, "height": 900},',
        '            locale="zh-CN"',
        '        )',
        '        page = await ctx.new_page()',
        '',
        '        print(f"NAV: {START_URL}")',
        '        await page.goto(START_URL, timeout=30000)',
        '        await smart_wait(page)',
        '',
        '        steps_to_run = STEPS',
        '        if step_filter:',
        '            steps_to_run = [s for s in STEPS if s.get("step_id") == step_filter]',
        '            if not steps_to_run:',
        '                print(f"FAIL 未找到步骤: {step_filter}")',
        '                return',
        '',
        '        success = 0',
        '        for i, step in enumerate(steps_to_run, 1):',
        '            print(f"\\n[{i}/{len(steps_to_run)}]", end="")',
        '            ok = await execute_step(page, step, context)',
        '            if ok:',
        '                success += 1',
        '',
        '        print(f"\\n{\"=\"*50}")',
        '        print(f"DONE: {success}/{len(steps_to_run)} 步骤成功")',
        '        print(f"{\"=\"*50}")',
        '        await browser.close()',
        '',
        '',
        'if __name__ == "__main__":',
        f'    parser = argparse.ArgumentParser(description={desc!r})',
        '    parser.add_argument("--var", nargs="+", default=[],',
        '                        help="动态变量, 如 --var key1=val1")',
        '    parser.add_argument("--headed", action="store_true", default=False,',
        '                        help="有头模式")',
        '    parser.add_argument("--step", default=None,',
        '                        help="只执行指定 step_id")',
        '    args = parser.parse_args()',
        '',
        '    ctx = dict(DEFAULT_VARIABLES)',
        '    for v in args.var:',
        '        if "=" in v:',
        '            k, val = v.split("=", 1)',
        '            ctx[k.strip()] = val.strip()',
        '',
        f'    print(f"START: {desc}")',
        '    print(f"   vars: {json.dumps(ctx, ensure_ascii=False)}")',
        '    asyncio.run(main(ctx, headed=args.headed, step_filter=args.step))',
        '',
    )

    return L


def main():
    parser = argparse.ArgumentParser(
        description="从 skill_config.json + steps.json 生成独立 Playwright 脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  generate_run.py --config ./my_skill/skill_config.json
  generate_run.py --config ./my_skill/skill_config.json --output /path/to/run.py
        """
    )
    parser.add_argument("--config", required=True, help="skill_config.json 路径")
    parser.add_argument("--output", default=None, help="输出路径 (default: <skill_dir>/run.py)")
    args = parser.parse_args()

    try:
        generate(args.config, args.output)
    except Exception as e:
        print(f"❌ 生成失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
