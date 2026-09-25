// 前端逻辑冒烟测试（需要 node）：
//   node tests/ui_smoke.js
// 用最小 DOM 桩执行 web/static/index.html 里的脚本，验证抽屉开关、
// 候选折叠、书架筛选等交互逻辑，不需要浏览器。
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web/static/index.html"), "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const code = scripts.join("\n");

class El {
  constructor(id) {
    this.id = id;
    this._classes = new Set();
    this.style = {};
    this.dataset = {};
    this.value = "";
    this.textContent = "";
    this._html = "";
    this.files = [];
    this.children = [];
    this.disabled = false;
  }
  get classList() {
    const c = this._classes;
    return {
      add: (...xs) => xs.forEach(x => c.add(x)),
      remove: (...xs) => xs.forEach(x => c.delete(x)),
      contains: x => c.has(x),
      toggle: x => (c.has(x) ? c.delete(x) : c.add(x)),
    };
  }
  set innerHTML(v) { this._html = String(v); if (v === "") this.children = []; }
  get innerHTML() { return this._html; }
  appendChild(x) { this.children.push(x); return x; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener() {}
  setAttribute() {}
  getAttribute() { return null; }
  closest() { return null; }
}

const ids = [...new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]))];
const els = {};
ids.forEach(id => { els[id] = new El(id); });
const body = new El("body");

global.document = {
  body,
  getElementById: id => els[id] || null,
  querySelectorAll: sel => (sel === ".drawer" ? [els["editor-card"], els["cover-card"]].filter(Boolean) : []),
  querySelector: sel => {
    if (sel === ".drawer.open") {
      return [els["editor-card"], els["cover-card"]].find(e => e && e.classList.contains("open")) || null;
    }
    return null;
  },
  addEventListener: () => {},
  createElement: tag => new El(tag),
};
global.window = { addEventListener: () => {} };
global.confirm = () => true;
global.alert = () => {};
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, sources: [], candidates: [], pending: [], outputs: [], failed: [] }) });

const results = [];
const check = (name, cond, extra) => results.push([name, !!cond, extra === undefined ? "" : String(extra)]);

const testCode = `
check("前端脚本可完整执行", true);
check("openDrawer 能打开编辑抽屉", (openDrawer("editor-card"), isOpen("editor-card")));
check("遮罩同时出现", $("drawer-mask").classList.contains("open"));
check("打开时锁定页面滚动", document.body.classList.contains("drawer-open"));
openDrawer("cover-card");
check("打开另一个抽屉会关掉前一个", !isOpen("editor-card") && isOpen("cover-card"));
closeDrawer("cover-card");
check("closeDrawer 能关闭", !isOpen("cover-card") && !$("drawer-mask").classList.contains("open"));
check("关闭后恢复页面滚动", !document.body.classList.contains("drawer-open"));

currentCands = [
  {title: "高分书甲", author: "作者A", score: 115, cover: "", source_label: "起点中文网"},
  {title: "低分书乙", author: "作者B", score: 30, cover: "", source_label: "豆瓣读书"},
  {title: "低分书丙", author: "作者C", score: 12, cover: "", source_label: "当当图书"},
];
renderCands();
const listHtml = $("cand-list").innerHTML;
check("高匹配候选默认显示", listHtml.indexOf("高分书甲") >= 0);
check("低匹配候选默认折叠", listHtml.indexOf('id="cand-low"') >= 0);
check("折叠按钮显示条数", listHtml.indexOf("显示其余 2 条低匹配候选") >= 0);
check("折叠区里仍包含低分候选", listHtml.indexOf("低分书乙") >= 0 && listHtml.indexOf("低分书丙") >= 0);
check("每张候选卡片 id 唯一", listHtml.indexOf('id="cand-0"') >= 0 && listHtml.indexOf('id="cand-2"') >= 0);

currentCands = [
  {title: "全是低分", author: "", score: 10, cover: "", source_label: "起点中文网"},
];
renderCands();
check("全是低分时不折叠（否则什么都看不到）",
      $("cand-list").innerHTML.indexOf('id="cand-low"') < 0 && $("cand-list").innerHTML.indexOf("全是低分") >= 0);

renderShelf([
  {name: "《十方天士》（校对版全本）作者： 逆苍天.txt", stem: "s1", title: "十方天士",
   author: "逆苍天", has_meta: true, has_cover: true},
  {name: "斗破苍穹全集txt下载.txt", stem: "s2", title: "斗破苍穹",
   author: "", has_meta: false, has_cover: true},
], false);
const pb = $("pending-body");
check("书架渲染出两行", pb.children.length === 2, pb.children.length);
const row0 = pb.children[0].innerHTML;
check("书名是主行（.bk-title）", row0.indexOf('class="bk-title"') >= 0 && row0.indexOf("十方天士") >= 0);
check("作者跟在书名后", row0.indexOf("逆苍天") >= 0);
check("文件名降为次要信息（.bk-file）", row0.indexOf('class="bk-file') >= 0);
check("文件名带 title 便于悬停看全名", row0.indexOf('title="《十方天士》') >= 0);
check("四个操作按钮齐全",
      ["scrape", "covers", "convert", "remove"].every(a => row0.indexOf('data-act="' + a + '"') >= 0));
check("元信息/封面徽标", row0.indexOf("元信息") >= 0 && row0.indexOf("封面") >= 0);
check("只有封面时显示封面徽标", pb.children[1].innerHTML.indexOf("封面") >= 0);
renderShelf([{name: "无名.txt", stem: "s3", title: "", author: "",
              has_meta: false, has_cover: false}], false);
check("元信息封面都没有时显示待刮削",
      $("pending-body").children[0].innerHTML.indexOf("待刮削") >= 0);
check("猜不出书名时显示未识别书名",
      $("pending-body").children[0].innerHTML.indexOf("未识别书名") >= 0);
renderShelf([
  {name: "《十方天士》（校对版全本）作者： 逆苍天.txt", stem: "s1", title: "十方天士",
   author: "逆苍天", has_meta: true, has_cover: true},
  {name: "斗破苍穹全集txt下载.txt", stem: "s2", title: "斗破苍穹",
   author: "", has_meta: false, has_cover: true},
], false);
check("操作列用 act-cell（不换行）", row0.indexOf('class="act-cell"') >= 0);
renderShelf([], true);
check("筛选无结果给筛选提示", $("pending-body").innerHTML.indexOf("没有匹配的书") >= 0);
renderShelf([], false);
check("书架为空给导入提示", $("pending-body").innerHTML.indexOf("书架为空") >= 0);

renderOutputs([
  {name: "《诡秘之主》-爱潜水的乌贼.epub", size: 13312000, mtime: "2026-09-25T10:30:00"},
]);
const ob = $("output-body");
check("输出结果渲染出一行", ob.children.length === 1, ob.children.length);
const outRow = ob.children[0].innerHTML;
check("输出文件名为主行", outRow.indexOf("《诡秘之主》") >= 0 && outRow.indexOf('class="bk-title"') >= 0);
check("输出含大小与时间", outRow.indexOf("MB") >= 0 && outRow.indexOf("2026-09-25") >= 0);
check("输出含下载链接与删除按钮",
      outRow.indexOf("/api/download/") >= 0 && outRow.indexOf('data-act="rmout"') >= 0);
renderOutputs([]);
check("输出为空时给提示", $("output-body").innerHTML.indexOf("还没有转换结果") >= 0);

// 布局：整页一个滚动条，书架全宽靠前
check("两栏并排区块 grid-2 存在", html.indexOf('class="grid-2"') >= 0);
check("并排卡片用 flex-start，矮的不会被拉高留空白",
      html.indexOf("align-items: flex-start") >= 0);
check("候选卡片作者/简介各自成行",
      html.indexOf(".cand .meta { display: block") >= 0 && html.indexOf(".cand .intro { display: block") >= 0);
check("临时截图预览代码已清理", html.indexOf("TEMP_SCREENSHOT") < 0);
check("书架排在导入之后、输出之前（首屏可见）",
      html.indexOf("书架 ") > html.indexOf("导入 TXT") &&
      html.indexOf("书架 ") < html.indexOf("输出结果"));
check("书架不再有内部滚动条", html.indexOf(".shelf-wrap") < 0 && html.indexOf(".col-side") < 0);
check("书架有独立状态条 shelf-status", html.indexOf('id="shelf-status"') >= 0);
check("书架表格不再用旧的宽表头", html.indexOf("<th>EPUB 文件</th>") < 0);
check("输出结果标题显示数量", html.indexOf('id="output-count"') >= 0);
check("书架操作提示已改到 shelf-status（不再是 import-status）",
      (html.split('$("shelf-status")').length - 1) >= 5 && html.indexOf('$("import-status")') >= 0,
      html.split('$("shelf-status")').length - 1);

el = $("shelf-filter"); el.value = "斗破";
check("书架筛选关键词读取", shelfKeyword() === "斗破");
el.value = "   ";
check("空白筛选词视为不过滤", shelfKeyword() === "");
el.value = "";
`;

try {
  eval(code + "\n" + testCode);
} catch (err) {
  check("执行期间抛异常", false, err && err.message);
}

let failed = 0;
for (const [name, ok, extra] of results) {
  if (!ok) failed++;
  console.log((ok ? "  [PASS] " : "  [FAIL] ") + name + (ok || !extra ? "" : "  -> " + extra));
}
console.log("\n共 " + results.length + " 项，" + (failed ? failed + " 项失败" : "全部通过"));
process.exit(failed ? 1 : 0);
