/* Claude Code の PostToolUse フック（Edit/Write の直後に走る）。
   - index.html: 埋め込みの <script> を取り出して文法だけ確かめる。
     単一HTMLなので、1文字の書き損じで利用者の画面が真っ白になる。push 前に止める。
   - sw.js: 文法に加え、HEAD から中身が変わったのに V が同じなら知らせる
     （V を上げないと旧キャッシュが残り続ける。HANDOVER §9）。
   問題があれば exit 2 で stderr を Claude に返す。編集自体は取り消さない。 */
const fs = require('fs'), path = require('path'), vm = require('vm');
const { execFileSync } = require('child_process');

let input = '';
process.stdin.on('data', d => input += d).on('end', () => {
  let file;
  try { file = JSON.parse(input).tool_input.file_path; } catch (e) { return; }
  if (!file) return;
  const root = path.resolve(__dirname, '..', '..');
  const rel = path.relative(root, path.resolve(file)).replace(/\\/g, '/');
  const problems = [];

  const check = (code, name, lineOffset) => {
    try { new vm.Script(code, { filename: name, lineOffset }); }
    catch (e) {
      const at = (e.stack || '').split('\n')[0];
      problems.push(`${at}\n  ${e.name}: ${e.message}`);
    }
  };

  if (rel === 'index.html') {
    const html = fs.readFileSync(path.join(root, rel), 'utf8');
    const re = /<script>([\s\S]*?)<\/script>/g;
    let m;
    while ((m = re.exec(html))) {
      const line = html.slice(0, m.index).split('\n').length - 1;
      check(m[1], 'index.html', line);
    }
  } else if (rel === 'sw.js') {
    const now = fs.readFileSync(path.join(root, rel), 'utf8');
    check(now, 'sw.js', 0);
    let head = null;
    try { head = execFileSync('git', ['show', 'HEAD:sw.js'], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }); }
    catch (e) {}
    const ver = s => (s.match(/^const V = '([^']*)'/m) || [])[1];
    if (head !== null && head.replace(/\r/g, '') !== now.replace(/\r/g, '') && ver(head) === ver(now)) {
      problems.push(`sw.js が HEAD から変わったが V は '${ver(now)}' のまま。push 前に V を上げること（HANDOVER §9）。`);
    }
  } else return;

  if (problems.length) {
    process.stderr.write(`[check-edit] ${rel}:\n` + problems.join('\n') + '\n');
    process.exit(2);
  }
});
