/* 狀態 / 錯誤 / 異動三頁共用的小工具。
 *
 * 沒有框架也沒有打包 —— 這幾頁只是把四個小 JSON 攤開給人看,拉一整包
 * 執行期進來只為了 innerHTML 並不划算,而且 gh-pages 上多一個大檔就是
 * 每次發布都要付的容量。
 *
 * 設計上只有一條規則值得記:**每個區塊自己負責自己的失敗**。四個 JSON
 * 是分開抓的,其中一個 404 或壞掉時,其他區塊照樣要能顯示 —— 狀態頁在
 * 「東西壞掉的時候」最需要能打得開。 */

/** UTC 時間字串 → 台灣時間。爬蟲寫出來的時間戳一律是 UTC(結尾的 Z)。 */
function taipei(iso) {
  const d = new Date(iso);
  if (!iso || isNaN(d)) return "—";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(d).replace(/\//g, "-");
}

/** 同上,只到日。 */
function taipeiDay(iso) {
  const full = taipei(iso);
  return full === "—" ? full : full.split(" ")[0];
}

/** 「3 小時前」。絕對時間看不出新舊,相對時間才是狀態頁真正要回答的。 */
function relative(iso) {
  const d = new Date(iso);
  if (!iso || isNaN(d)) return "—";
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "剛剛";
  if (mins < 60) return mins + " 分鐘前";
  const hours = Math.round(mins / 60);
  if (hours < 48) return hours + " 小時前";
  return Math.round(hours / 24) + " 天前";
}

/** 距今幾小時,拿來判斷資料新不新。時間無效時回傳 Infinity(當成很舊)。 */
function hoursSince(iso) {
  const d = new Date(iso);
  if (!iso || isNaN(d)) return Infinity;
  return (Date.now() - d.getTime()) / 3600000;
}

/** 秒 → 「8 分 38 秒」。 */
function duration(seconds) {
  if (typeof seconds !== "number" || !isFinite(seconds)) return "—";
  const s = Math.round(seconds);
  if (s < 60) return s + " 秒";
  const m = Math.floor(s / 60);
  if (m < 60) return m + " 分 " + (s % 60) + " 秒";
  return Math.floor(m / 60) + " 小時 " + (m % 60) + " 分";
}

/** 千分位。**null 與 undefined 不當成 0** —— 交接文件講過,
 *  「跑了但沒抓到」和「根本沒這個欄位」是兩回事,不可以都顯示 0。 */
function num(v) {
  return typeof v === "number" ? v.toLocaleString("en-US") : "—";
}

/** 這些字串全部來自爬蟲抓下來的學校資料(課名、教師姓名、錯誤訊息),
 *  一律當成不可信,進 innerHTML 前先跳脫。 */
function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** "114-2" → 可比大小的數字。學期字串直接排序會把 90-1 排在 114-1 後面。 */
function semesterKey(s) {
  const m = String(s).match(/^(\d+)-(\d+)$/);
  return m ? Number(m[1]) * 10 + Number(m[2]) : 0;
}

function byId(id) {
  return document.getElementById(id);
}

/** 讀一個發布在同目錄下的 JSON。
 *
 * `no-store` 是刻意的:狀態頁的重點就是「現在如何」,讀到瀏覽器快取裡
 * 半小時前的檔會直接讓這頁失去意義。 */
async function loadJSON(name) {
  const res = await fetch(name, { cache: "no-store" });
  if (!res.ok) throw new Error(name + " HTTP " + res.status);
  return res.json();
}

/** 把一個已經在抓的 JSON 渲染進某個區塊,失敗只壞這一塊。
 *
 * 傳進來的是 promise 而不是檔名 —— 這樣同一份資料可以餵給多個區塊,
 * 只抓一次。 */
async function block(id, promise, render) {
  const node = byId(id);
  if (!node) return;
  try {
    render(node, await promise);
  } catch (err) {
    node.innerHTML =
      '<p class="empty failed">讀不到資料:' + esc(err.message) + "</p>";
  }
}

/** 綁「重新整理」按鈕,並記下這頁的資料是什麼時候讀的。
 *
 * 不自動輪詢:這幾個檔最快也要 4 小時才會變,每分鐘去打一次只是浪費
 * 別人的流量。要看最新的,按一下。 */
function boot(render) {
  const run = () => {
    const stamp = byId("loaded-at");
    if (stamp) stamp.textContent = "資料讀取於 " + taipei(new Date().toISOString());
    render();
  };
  const button = byId("refresh");
  if (button) button.addEventListener("click", run);
  run();
}


/** 表格「先顯示幾列,其餘用按鈕展開」。
 *
 * 三張表共用同一套,而且**一定是同一張表裡的列**,絕不拆成第二張表 ——
 * 兩張表各自計算欄寬,展開後每一欄都會對不齊,而且第二張沒有表頭。
 * (`<details>` 不能合法包住 `<tr>`,所以「用 details 收起多餘的列」這條路
 * 走不通,只能靠 hidden。)
 *
 * `label(n)` 收到的是被藏起來的列數,回傳按鈕上的文字。
 */
function collapsibleRows(node, visible, label) {
  const rows = Array.from(node.querySelectorAll("tbody tr"));
  if (rows.length <= visible) return;

  const hide = shouldHide => {
    rows.forEach((tr, i) => { if (i >= visible) tr.hidden = shouldHide; });
  };
  hide(true);

  const closed = label(rows.length - visible);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "more";
  button.textContent = closed;

  let open = false;
  button.addEventListener("click", () => {
    open = !open;
    hide(!open);
    button.textContent = open ? "收合" : closed;
  });
  node.appendChild(button);
}
