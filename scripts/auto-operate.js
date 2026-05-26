#!/usr/bin/env node
/**
 * auto-operate.js - [Legacy v1.0] 自动化操作录制脚本
 * 
 * ⚠️ DEPRECATED: 此文件为 v1.0 遗留版本，使用 playwright-cli 命令行。
 *    推荐使用 v2.0 版本：
 *    - recorder.py  → Playwright 异步录制
 *    - playback.py  → 多策略回放
 *    - generate_run.py → 独立脚本生成
 * 
 * 使用 playwright-cli 命令行执行所有操作
 * 
 * 环境变量:
 *   PLAYWRIGHT_CLI_SESSION - playwright-cli 会话名，默认 'auto-op'
 *   NODE_PATH - 需包含 /home/aicase/.npm-global/lib/node_modules
 */
const { execSync, spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const SESSION = process.env.PLAYWRIGHT_CLI_SESSION || 'auto-op';
const OP_LOG = [];  // 操作日志

const LOG = (msg) => console.log(`[${new Date().toLocaleTimeString('zh-CN')}] ${msg}`);
const ERR = (msg) => console.error(`❌ ${msg}`);

/**
 * 执行 playwright-cli 命令
 */
function run(args, opts = {}) {
    const cmd = ['playwright-cli', `-s=${SESSION}`, ...args];
    LOG(`  $ ${cmd.join(' ')}`);
    try {
        return execSync(cmd.join(' '), {
            encoding: 'utf8',
            timeout: 30000,
            stdio: 'pipe',
            ...opts
        });
    } catch (e) {
        if (e.status !== 0 && opts.strict !== false) {
            ERR(`命令失败: ${e.message.split('\n')[0]}`);
        }
        return e.stdout || '';
    }
}

/**
 * 记录操作步骤
 */
function logStep(action, target, params, note = '') {
    const step = {
        action,
        target,
        params: params || '',
        note
    };
    OP_LOG.push(step);
    const n = OP_LOG.length;
    LOG(`📝 步骤${n}记录: ${action} ${target} ${params || ''} ${note ? '// ' + note : ''}`);
}

/**
 * 获取页面快照（带 ref）
 */
function snapshot(filename) {
    const fn = filename || `/tmp/autoop_snapshot_${Date.now()}.yaml`;
    const result = run(['snapshot', '--filename=' + fn, '--depth=4'], { strict: false });
    logStep('snapshot', fn, '', '获取元素 ref');
    return { result, file: fn };
}

/**
 * 截图
 */
function screenshot(filename) {
    const fn = filename || `/tmp/autoop_ss_${Date.now()}.png`;
    run(['screenshot', fn, '--filename=' + fn]);
    logStep('screenshot', fn);
    LOG(`📸 ${fn}`);
    return fn;
}

/**
 * 打开网址（全屏）
 */
function open(url, opts = {}) {
    const args = ['open', url];
    if (opts.headed) args.push('--headed');
    const result = run(args, { stdio: 'inherit' });
    logStep('open', url);
    return result;
}

/**
 * 导航到 URL
 */
function goto(url) {
    const result = run(['goto', url]);
    logStep('goto', url);
    return result;
}

/**
 * 点击元素
 */
function click(target, button = 'left') {
    const result = run(['click', target, button]);
    logStep('click', target);
    return result;
}

/**
 * 填入文字
 */
function fill(target, text) {
    const result = run(['fill', target, text]);
    logStep('fill', target, text);
    return result;
}

/**
 * 输入文字（逐字）
 */
function type(text) {
    const result = run(['type', text]);
    logStep('type', text);
    return result;
}

/**
 * 按键
 */
function press(key) {
    const result = run(['press', key]);
    logStep('press', key);
    return result;
}

/**
 * 悬停
 */
function hover(target) {
    const result = run(['hover', target]);
    logStep('hover', target);
    return result;
}

/**
 * 下拉选择
 */
function select(target, value) {
    const result = run(['select', target, value]);
    logStep('select', target, value);
    return result;
}

/**
 * 选中 checkbox/radio
 */
function check(target) {
    const result = run(['check', target]);
    logStep('check', target);
    return result;
}

/**
 * 取消选中
 */
function uncheck(target) {
    const result = run(['uncheck', target]);
    logStep('uncheck', target);
    return result;
}

/**
 * 鼠标移动
 */
function mousemove(x, y) {
    const result = run(['mousemove', String(x), String(y)]);
    logStep('mousemove', `${x},${y}`);
    return result;
}

/**
 * 刷新
 */
function reload() {
    const result = run(['reload']);
    logStep('reload', '');
    return result;
}

/**
 * 后退
 */
function goBack() {
    const result = run(['go-back']);
    logStep('go-back', '');
    return result;
}

/**
 * 前进
 */
function goForward() {
    const result = run(['go-forward']);
    logStep('go-forward', '');
    return result;
}

/**
 * 接受弹窗
 */
function dialogAccept(prompt) {
    const result = run(['dialog-accept', ...(prompt ? [prompt] : [])]);
    logStep('dialog-accept', prompt || '');
    return result;
}

/**
 * 拒绝弹窗
 */
function dialogDismiss() {
    const result = run(['dialog-dismiss']);
    logStep('dialog-dismiss', '');
    return result;
}

/**
 * 等待（秒）
 */
function sleep(seconds) {
    return new Promise(r => setTimeout(r, seconds * 1000));
}

/**
 * 调整窗口大小
 */
function resize(w, h) {
    const result = run(['resize', String(w), String(h)]);
    logStep('resize', `${w}x${h}`);
    return result;
}

/**
 * 等待加载
 */
function waitLoad(seconds = 3) {
    run(['sleep', String(seconds)]);
    logStep('sleep', String(seconds));
}

/**
 * 执行 JS
 */
function evalCode(code) {
    const result = run(['eval', code]);
    logStep('eval', code.replace(/\n/g, ' ').substring(0, 50));
    return result;
}

/**
 * 加载登录状态
 */
function stateLoad(authFile) {
    const result = run(['state-load', authFile]);
    logStep('state-load', authFile);
    return result;
}

/**
 * 上传文件
 */
function upload(target, filePath) {
    const result = run(['upload', target, '--path=' + filePath]);
    logStep('upload', target, filePath);
    return result;
}

/**
 * 获取操作日志（生成脚本用）
 */
function getOpLog() {
    return OP_LOG;
}

/**
 * 导出操作日志到文件
 */
function exportLog(outputFile = '/tmp/autoop_log.json') {
    fs.writeFileSync(outputFile, JSON.stringify(OP_LOG, null, 2));
    LOG(`📋 操作日志已保存: ${outputFile}`);
    return outputFile;
}

/**
 * 生成 Node.js 脚本（供后续复用）
 */
function generateScript(skillName, outputPath) {
    const lines = [
        '#!/usr/bin/env node',
        `/***`,
        ` * ${skillName} - 自动生成脚本`,
        ` * 创建时间: ${new Date().toLocaleString('zh-CN')}`,
        ` * 使用方式: node ${path.basename(outputPath)}`,
        ` */`,
        '',
        `const SESSION = '${SESSION}';`,
        `const LOG = (msg) => console.log(\`[\${new Date().toLocaleTimeString('zh-CN')}] \${msg}\`);`,
        '',
        `function run(args) {`,
        `    const { execSync } = require('child_process');`,
        `    const cmd = ['playwright-cli', \`-s=\${SESSION}\`, ...args];`,
        `    return execSync(cmd.join(' '), { encoding: 'utf8', timeout: 30000 });`,
        `}`,
        '',
        'async function main() {',
        `    LOG('开始执行...');`,
        ''
    ];

    for (const op of OP_LOG) {
        const action = op.action;
        const target = op.target ? ` '${op.target}'` : '';
        const params = op.params ? `, '${op.params}'` : '';
        
        if (action === 'sleep') {
            lines.push(`    await new Promise(r => setTimeout(r, ${op.target * 1000}));`);
        } else if (action === 'snapshot' || action === 'screenshot') {
            lines.push(`    run(['${action}', '${op.target}']);`);
        } else if (['open', 'goto', 'click', 'fill', 'type', 'press', 'hover', 'select', 
                     'check', 'uncheck', 'mousemove', 'reload', 'go-back', 'go-forward',
                     'dialog-accept', 'dialog-dismiss', 'resize', 'eval', 'state-load', 'upload'].includes(action)) {
            lines.push(`    run(['${action}'${target}${params}]);`);
        }
    }

    lines.push('    LOG("✅ 执行完成");');
    lines.push('}');
    lines.push('');
    lines.push('main().catch(e => { console.error("❌", e.message); process.exit(1); });');

    const content = lines.join('\n');
    fs.writeFileSync(outputPath, content);
    LOG(`📄 脚本已生成: ${outputPath}`);
    return outputPath;
}

// 调试：打印当前会话状态
function status() {
    try {
        const out = run(['list']);
        LOG('📋 会话状态:\n' + out);
    } catch(e) {}
}

module.exports = {
    run, snapshot, screenshot, open, goto, click, fill, type, press,
    hover, select, check, uncheck, mousemove, reload, goBack, goForward,
    dialogAccept, dialogDismiss, sleep, resize, waitLoad, evalCode,
    stateLoad, upload, getOpLog, exportLog, generateScript, status,
    SESSION, logStep
};