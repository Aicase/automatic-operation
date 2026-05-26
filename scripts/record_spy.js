/**
 * record_spy.js — 前端探针拦截脚本 (Enhanced v2.0)
 *
 * 功能增强：
 *   1. 鼠标悬停时蓝色虚线高亮可选元素
 *   2. 点击拦截 → 多维特征提取 → onElementCaptured 回调推送
 *   3. 键盘事件捕获（Enter/Tab/Escape → 记录 press 动作）
 *   4. Shadow DOM 穿透提取 innerText
 *   5. 关联 <label> 文本提取（提升 label 策略定位稳定性）
 *   6. 视觉闪烁反馈（绿色确认 / 红色警告）
 *
 * Playwright 注入方式：
 *   await page.add_init_script(spy_code)
 *   await page.expose_function('onElementCaptured', async (data) => { ... })
 *
 * Keyboard 注入方式：
 *   await page.expose_function('onKeyCaptured', async (data) => { ... })
 */
(() => {
  if (window.__openclaw_spy_loaded__) return;
  window.__openclaw_spy_loaded__ = true;

  let lastHovered = null;
  let _prevOutline = '';
  let _shiftDown = false;
  let _ctrlDown = false;

  // ─── 键盘修饰键追踪 ──────────────────────────────────
  document.addEventListener('keydown', (e) => {
    _shiftDown = e.shiftKey;
    _ctrlDown = e.ctrlKey || e.metaKey;
  }, { passive: true });
  document.addEventListener('keyup', (e) => {
    _shiftDown = e.shiftKey;
    _ctrlDown = e.ctrlKey || e.metaKey;
  }, { passive: true });

  // ─── 隐式无障碍角色推断 (覆盖所有 HTML5 标准元素) ─────
  function getImplicitRole(el) {
    const explicitRole = el.getAttribute('role');
    if (explicitRole) return explicitRole;

    const tag = el.tagName.toLowerCase();
    const type = (el.type || '').toLowerCase();

    // 链接
    if (tag === 'a' && el.href) return 'link';
    if (tag === 'a') return 'link';

    // 按钮类
    if (tag === 'button') return 'button';
    if (tag === 'input' && ['submit', 'button', 'reset', 'image'].includes(type)) return 'button';

    // 输入类
    if (tag === 'input' || tag === 'textarea') {
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'file') return 'textbox';
      if (type === 'color') return 'textbox';
      if (type === 'date' || type === 'datetime-local' || type === 'month' || type === 'week' || type === 'time') return 'textbox';
      return 'textbox';
    }

    // 选择器
    if (tag === 'select') return 'combobox';

    // 表格相关
    if (tag === 'table') return 'table';
    if (tag === 'tr') return 'row';
    if (tag === 'td' || tag === 'th') return 'cell';
    if (tag === 'thead' || tag === 'tbody' || tag === 'tfoot') return 'rowgroup';

    // 语义元素
    if (tag === 'nav') return 'navigation';
    if (tag === 'main') return 'main';
    if (tag === 'header') return (el.closest('article') || el.closest('section')) ? 'group' : 'banner';
    if (tag === 'footer') return (el.closest('article') || el.closest('section')) ? 'group' : 'contentinfo';
    if (tag === 'aside') return 'complementary';
    if (tag === 'section') return 'region';
    if (tag === 'article') return 'article';
    if (tag === 'form') return 'form';
    if (tag === 'fieldset') return 'group';
    if (tag === 'details') return 'group';
    if (tag === 'summary') return 'button';
    if (tag === 'dialog') return 'dialog';
    if (tag === 'figure') return 'figure';
    if (tag === 'img') return 'img';
    if (tag === 'ul' || tag === 'ol' || tag === 'dl') return 'list';
    if (tag === 'li' || tag === 'dt' || tag === 'dd') return 'listitem';

    return tag;
  }

  // ─── 动态 ID 检测 ────────────────────────────────────
  function isDynamicId(id) {
    if (!id || typeof id !== 'string') return true;
    // 前端框架自动生成模式
    if (/^\d+$/.test(id)) return true;                          // 纯数字
    if (/[0-9]{5,}$/.test(id)) return true;                     // 5+ 位数字结尾
    if (/\b[a-f0-9]{8,}\b/i.test(id)) return true;              // 8+ hex 随机串
    if (/^(css-|class-|sc-|Styled|react-|ember|ember-view)/.test(id)) return true;
    if (/^[A-Z].+[0-9]{4,}$/.test(id)) return true;             // 大写开头+数字尾
    if (id.length > 30 && id.includes('-')) return true;         // 超长带横线
    return false;
  }

  // ─── 稳定 test_id 特征提取 ────────────────────────────
  function getTestIdFeature(el) {
    const testId = el.getAttribute('data-testid')
                || el.getAttribute('data-test-id')
                || el.getAttribute('data-cy')
                || el.getAttribute('data-qa')
                || el.getAttribute('data-test')
                || null;
    if (testId) return testId;

    // id 仅当非动态时使用
    if (el.id && !isDynamicId(el.id)) return el.id;

    return null;
  }

  // ─── 关联 Label 文本提取 ──────────────────────────────
  function getLabelText(el) {
    // 1. 通过 id → <label for="id"> 关联
    if (el.id) {
      try {
        const escapedId = CSS.escape(el.id);
        const label = document.querySelector(`label[for="${escapedId}"]`);
        if (label) return label.innerText.trim().substring(0, 100);
      } catch {
        // CSS.escape 可能对某些特殊字符抛异常，静默跳过
      }
    }

    // 2. 被 <label> 包裹的情况
    const parentLabel = el.closest('label');
    if (parentLabel) {
      // 提取 label 文本但排除 el 自身的文本
      const clone = parentLabel.cloneNode(true);
      const inputLike = clone.querySelector('input,select,textarea');
      if (inputLike) inputLike.remove();
      const text = clone.innerText.trim();
      if (text) return text.substring(0, 100);
    }

    // 3. aria-labelledby 关联
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const ids = labelledBy.split(/\s+/);
      const texts = ids.map(id => {
        const labeled = document.getElementById(id);
        return labeled ? labeled.innerText.trim() : '';
      }).filter(Boolean);
      if (texts.length > 0) return texts.join(' ').substring(0, 100);
    }

    return null;
  }

  // ─── Shadow DOM 穿透 innerText（限制遍历节点数防止大页面卡顿）
  function getDeepText(el) {
    try {
      let text = '';
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
      let count = 0;
      while (walker.nextNode() && count < 500) {
        const nodeText = walker.currentNode.textContent.trim();
        if (nodeText) text += nodeText + ' ';
        count++;
      }
      const result = text.trim() || el.innerText || '';
      return result.substring(0, 300);
    } catch {
      return (el.innerText || '').substring(0, 300);
    }
  }

  // ─── 推断动作类型 ─────────────────────────────────────
  function inferAction(el, role) {
    const tag = el.tagName.toLowerCase();
    const type = (el.type || '').toLowerCase();

    // 可输入元素 → fill
    if (role === 'textbox') return 'fill';
    if (tag === 'textarea') return 'fill';
    if (tag === 'input' && !['checkbox', 'radio', 'submit', 'button', 'reset', 'image'].includes(type)) return 'fill';

    // 选择器
    if (role === 'combobox' || tag === 'select') return 'select';

    // 勾选/单选
    if (role === 'checkbox' || type === 'checkbox') return 'check';
    if (role === 'radio' || type === 'radio') return 'check';

    // 链接/按钮
    if (role === 'link') return 'click';
    if (role === 'button') return 'click';
    if (tag === 'summary') return 'click';

    // 可点击的通用元素（有 href 或 onclick 或有语义角色暗示交互）
    if (el.href || el.onclick || el.getAttribute('onclick')) return 'click';

    // details > summary 等展开交互
    if (el.closest('details') && tag === 'summary') return 'click';

    return 'click';
  }

  // ─── 元素类型分类（供 AI 决策参考）───────────────────
  function classifyElementType(el, role) {
    const tag = el.tagName.toLowerCase();
    const type = (el.type || '').toLowerCase();

    if (role === 'textbox' || tag === 'textarea' || (tag === 'input' && !['checkbox','radio','submit','button','reset','image','hidden'].includes(type))) {
      return 'text_input';
    }
    if (role === 'combobox' || tag === 'select') return 'dropdown';
    if (role === 'checkbox' || type === 'checkbox') return 'checkbox';
    if (role === 'radio' || type === 'radio') return 'radio_button';
    if (role === 'button' || tag === 'a' || tag === 'button') return 'action_button';
    if (role === 'link') return 'navigation_link';
    return 'interactive_element';
  }

  // ─── HTML 片段提取（壳结构 + 关键属性）───────────────
  function getHtmlFragment(el) {
    try {
      const tag = el.tagName.toLowerCase();
      const attrs = [];
      // 只保留定位相关属性
      const keyAttrs = ['id', 'class', 'name', 'type', 'placeholder', 'role',
                        'aria-label', 'data-testid', 'data-test-id', 'data-cy', 'data-qa',
                        'href', 'title', 'alt', 'value'];
      for (const attr of keyAttrs) {
        const val = el.getAttribute(attr);
        if (val !== null && val !== undefined) {
          attrs.push(`${attr}="${val.substring(0, 80)}"`);
        }
      }

      let html = `<${tag} ${attrs.join(' ')}`;
      // 子节点指示
      const childCount = el.children.length;
      const childTags = [];
      for (const child of el.children) {
        childTags.push(child.tagName.toLowerCase());
        if (childTags.length >= 3) break;
      }
      if (childCount > 0) {
        html += `>…(${childCount} children: ${childTags.join(', ')}…)`;
      } else if (el.innerText) {
        html += `>"${el.innerText.trim().substring(0, 50)}"`;
      }
      html += `</${tag}>`;
      return html.substring(0, 250);
    } catch {
      return '<!-- element access denied -->';
    }
  }

  // ─── CSS 类名稳定部分提取 ────────────────────────────
  function getStableClassNames(el) {
    if (!el.className || typeof el.className !== 'string') return '';
    const classes = el.className.split(/\s+/).filter(c => c && c.length > 0);
    // 过滤动态哈希类名（__xxx 哈希后缀、css-xxx 随机串、Styled 组件）
    const stable = classes.filter(c => {
      if (/^css-[\da-z]+$/i.test(c) && c.length > 8) return false;
      if (/^sc-[\da-zA-Z]+$/.test(c)) return false;
      if (/^[A-Z][a-z]+__[a-z]+___[\da-zA-Z]+$/.test(c)) return false;
      if (/_[\da-f]{5,}$/.test(c)) return false;
      return true;
    });
    return stable.join(' ');
  }

  // ─── 构建完整 payload ────────────────────────────────
  function buildPayload(el) {
    const role = getImplicitRole(el);
    const testId = getTestIdFeature(el);
    const labelText = getLabelText(el);
    const deepText = getDeepText(el);
    const elementType = classifyElementType(el, role);
    const stableClasses = getStableClassNames(el);

    return {
      tagName: el.tagName.toLowerCase(),
      role: role,
      elementType: elementType,
      text: deepText,
      labelText: labelText,
      testId: testId,
      placeholder: el.getAttribute('placeholder') || null,
      ariaLabel: el.getAttribute('aria-label')
              || el.getAttribute('title')
              || el.getAttribute('alt')
              || null,
      htmlFragment: getHtmlFragment(el),
      // 定位辅助字段
      id: el.id || null,
      isDynamicId: el.id ? isDynamicId(el.id) : true,
      name: el.getAttribute('name') || null,
      className: stableClasses || null,
      href: el.getAttribute('href') || null,
      type: (el.type || '').toLowerCase() || null,
      // 推断动作
      action: inferAction(el, role),
      // 修饰键状态
      modifiers: {
        shift: _shiftDown,
        ctrl: _ctrlDown
      },
      // 可见性与坐标
      boundingBox: (() => {
        try {
          const r = el.getBoundingClientRect();
          return { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) };
        } catch { return null; }
      })(),
      visible: (() => {
        try {
          const style = window.getComputedStyle(el);
          return style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity) > 0;
        } catch { return true; }
      })()
    };
  }

  // ─── 高亮悬停（使用 mouseenter/mouseleave 避免子元素闪烁）
  document.addEventListener('mouseover', (e) => {
    const el = e.target;
    if (!el || el === document.body || el === document.documentElement) return;
    if (el === lastHovered) return;
    // 检查是否仍在同一个顶层元素内（防止子元素间切换重复高亮）
    if (lastHovered && lastHovered.contains(el)) return;

    if (lastHovered) {
      lastHovered.style.outline = _prevOutline;
      lastHovered.style.cursor = '';
    }

    lastHovered = el;
    _prevOutline = el.style.outline || '';
    el.style.outline = '2px dashed #2563eb';
    el.style.cursor = 'pointer';
  }, { passive: true });

  // 用 mousemove 做离开检测，比 mouseout 更可靠
  document.addEventListener('mousemove', (e) => {
    if (!lastHovered) return;
    // 检查鼠标是否已经离开 lastHovered 及其所有子元素
    if (!lastHovered.contains(e.target) && e.target !== lastHovered) {
      lastHovered.style.outline = _prevOutline;
      lastHovered.style.cursor = '';
      lastHovered = null;
    }
  }, { passive: true });

  // ─── 拦截点击（捕获阶段）──────────────────────────────
  document.addEventListener('click', (e) => {
    const el = e.target;
    if (!el || el === document.body || el === document.documentElement) return;

    // 阻止默认跳转行为
    e.preventDefault();
    e.stopPropagation();

    const payload = buildPayload(el);

    // 视觉确认反馈（绿色闪烁）
    const prevOutline = el.style.outline;
    el.style.outline = '3px solid #10b981';
    setTimeout(() => { el.style.outline = prevOutline; }, 500);

    // 推送给 Python 端
    if (typeof window.onElementCaptured === 'function') {
      window.onElementCaptured(payload);
    }
  }, true);

  // ─── 键盘事件捕获（只拦截 Enter，Tab/Escape/方向键不阻止默认行为）
  document.addEventListener('keydown', (e) => {
    const active = document.activeElement;
    if (!active) return;

    const keyActions = ['Enter', 'Tab', 'Escape', 'PageDown', 'PageUp', 'ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'];
    if (!keyActions.includes(e.key)) return;

    const tag = active.tagName.toLowerCase();

    // 只在输入类元素中记录（避免全局误触发）
    if (!['input', 'textarea', 'select'].includes(tag) && active.getAttribute('contenteditable') !== 'true') return;

    // ✅ 只拦截 Enter（阻止表单提交），Tab/Escape/方向键保留默认行为
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
    }

    const payload = buildPayload(active);
    payload.action = 'press';
    payload.keyPressed = e.key;

    // 视觉反馈（蓝色脉冲）
    const prevOutline = active.style.outline;
    active.style.outline = '3px solid #3b82f6';
    setTimeout(() => { active.style.outline = prevOutline; }, 300);

    if (typeof window.onKeyCaptured === 'function') {
      window.onKeyCaptured(payload);
    }
  }, true);
})();
