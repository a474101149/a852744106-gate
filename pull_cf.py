#!/usr/bin/env python3
# CF 分国优选/中转名单拉取器 (ming 自用)
# 每轮: 拉公开分国名单 -> 按国家去重 -> TCP 测活 -> 生成 nodes.txt + data.json
# 只用标准库, 无需 pip。测活只代表"从运行这台机器出发能连通", 不代表客户端一定快。
import concurrent.futures, json, re, socket, sys, time, urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "cfpull/1.0"}
TIMEOUT_FETCH = 20
TIMEOUT_TCP = 3
MAX_PER_COUNTRY = 15

COUNTRIES = {  # code -> 中文名
    "JP": "日本", "KR": "韩国", "SG": "新加坡", "US": "美国", "GB": "英国",
}
# 固定占位 IP, 每份名单第一行都是它, 与国家无关, 剔除
SKIP_HOSTS = {"162.159.198.1"}

# (国家, 来源名, URL) —— 顺序即优先级: 联通向实测源在前
SOURCES = [
    ("JP", "tiancheng", "https://edt-aio-nav.pages.dev/tiancheng/jp.txt"),
    ("KR", "tiancheng", "https://edt-aio-nav.pages.dev/tiancheng/kr.txt"),
    ("SG", "tiancheng", "https://edt-aio-nav.pages.dev/tiancheng/sg.txt"),
    ("US", "tiancheng", "https://edt-aio-nav.pages.dev/tiancheng/us.txt"),
    ("JP", "gslege", "https://edt-aio-nav.pages.dev/gslege/JP.txt"),
    ("SG", "gslege", "https://edt-aio-nav.pages.dev/gslege/SG.txt"),
    ("US", "gslege", "https://edt-aio-nav.pages.dev/gslege/US.txt"),
]
# JZ 源: 一份文件混多国, 国家写在备注里 (#JP / #KR ...), 联通向, 半小时更新
JZ_FILES = [
    ("jz-best", "https://raw.githubusercontent.com/HandsomeMJZ/cfip/refs/heads/main/best_ips.txt"),
    ("jz-full", "https://raw.githubusercontent.com/HandsomeMJZ/cfip/refs/heads/main/full_ips.txt"),
]

HOSTPORT = re.compile(r"^([A-Za-z0-9.\-]+):(\d{2,5})\b")
TAG = re.compile(r"#\s*(JP|KR|SG|US|GB|UK)\b")

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT_FETCH) as r:
        return r.read().decode("utf-8", "ignore")

def parse_lines(text, fixed_country=None):
    out = []
    for line in text.splitlines():
        line = line.strip()
        m = HOSTPORT.match(line)
        if not m:
            continue
        host, port = m.group(1), int(m.group(2))
        if host in SKIP_HOSTS or not (1 <= port <= 65535):
            continue
        cc = fixed_country
        if cc is None:
            t = TAG.search(line)
            if not t:
                continue
            cc = "GB" if t.group(1) == "UK" else t.group(1)
        out.append((cc, host, port))
    return out

def tcp_test(item):
    cc, host, port, src = item
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_TCP):
            return (cc, host, port, src, round((time.time() - t0) * 1000))
    except Exception:
        return None

def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    pool = {}   # (cc, host, port) -> source (先到先得 = 高优先级)
    errors = []
    for cc, name, url in SOURCES:
        try:
            for c2, h, p in parse_lines(fetch(url), fixed_country=cc):
                pool.setdefault((c2, h, p), name)
        except Exception as e:
            errors.append(f"{name}/{cc}: {e}")
    for name, url in JZ_FILES:
        try:
            for c2, h, p in parse_lines(fetch(url)):
                if c2 in COUNTRIES:
                    pool.setdefault((c2, h, p), name)
        except Exception as e:
            errors.append(f"{name}: {e}")

    items = [(cc, h, p, s) for (cc, h, p), s in pool.items()]
    alive = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=60) as ex:
        for r in ex.map(tcp_test, items):
            if r:
                alive.append(r)

    by_cc = {cc: [] for cc in COUNTRIES}
    for cc, h, p, s, ms in alive:
        by_cc[cc].append((ms, h, p, s))
    lines, summary = [], {}
    for cc in ["JP", "KR", "SG", "US", "GB"]:
        lst = sorted(by_cc[cc])[:MAX_PER_COUNTRY]
        summary[cc] = {"candidates": sum(1 for i in items if i[0] == cc),
                       "alive": len(by_cc[cc]), "kept": len(lst)}
        for i, (ms, h, p, s) in enumerate(lst, 1):
            lines.append(f"{h}:{p}#{COUNTRIES[cc]}-{i:02d}")
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(f"{out_dir}/nodes.txt", "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    with open(f"{out_dir}/data.json", "w") as f:
        json.dump({"generated_at": gen, "total_kept": len(lines),
                   "countries": summary, "errors": errors}, f, ensure_ascii=False, indent=1)
    print(f"[{gen}] kept={len(lines)} " +
          " ".join(f"{cc}:{summary[cc]['kept']}/{summary[cc]['alive']}/{summary[cc]['candidates']}" for cc in summary))
    if errors:
        print("source errors:", "; ".join(errors), file=sys.stderr)

if __name__ == "__main__":
    main()
