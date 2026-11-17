#!/usr/bin/env python3
# CF 分国优选/中转名单拉取器 (ming 自用)
# 每轮: 拉公开分国名单 -> 按国家去重 -> TCP 测活 -> 生成 nodes.txt + data.json
# 只用标准库, 无需 pip。测活只代表"从运行这台机器出发能连通", 不代表客户端一定快。
import concurrent.futures, ipaddress, json, re, socket, sys, time, urllib.request
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

# 稳定模式 (ming 2026-11-17 要求稳定优先): 只保留 Cloudflare 官方任播 IP 段 + 443 端口。
# 官方 IP 不会下线, 稳定性最高; 代价: 靠别人中转机落国的条目 (多为韩国/英国, 非 443 怪端口) 会被剔除,
# 国家标注变为"该 IP 的惯常落点", 不保证每次都落该国。
STABLE_ONLY = True
CF_NETS = [ipaddress.ip_network(c) for c in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22")]

def is_stable(host, port):
    if port != 443:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 域名条目不进稳定池
    return any(ip in n for n in CF_NETS)

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

    items = [(cc, h, p, s) for (cc, h, p), s in pool.items()
             if not STABLE_ONLY or is_stable(h, p)]
    alive = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=60) as ex:
        for r in ex.map(tcp_test, items):
            if r:
                alive.append(r)

    by_cc = {cc: [] for cc in COUNTRIES}
    for cc, h, p, s, ms in alive:
        by_cc[cc].append((ms, h, p, s))
    lines, summary = [], {}
    seen_hp = set()  # 同一 IP:端口 只归一个国家 (任播 IP 被多份名单标成不同国家时, 按 JP>KR>SG>US>GB 取先)
    for cc in ["JP", "KR", "SG", "US", "GB"]:
        lst = []
        for entry in sorted(by_cc[cc]):
            hp = (entry[1], entry[2])
            if hp in seen_hp:
                continue
            seen_hp.add(hp)
            lst.append(entry)
            if len(lst) >= MAX_PER_COUNTRY:
                break
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
