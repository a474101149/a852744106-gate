#!/usr/bin/env python3
# CF 分国优选名单拉取器 v3 (ming 自用)
# v3 关键修正 (2026-11-17 实测教训): 只测 TCP 连通没用 —— 大量名单 IP 对 ming 自己的域名
# 返回 Cloudflare 1034 (Edge IP Restricted), 客户端连上也是 -1。名单作者是用他们自己的
# 域名/speed.cloudflare.com 测的, 对别人的域名不作数。
# 所以本版测活 = 拿 ming 自己的域名做 SNI+Host 实测 HTTPS, 返回 200 (Worker 的 hello) 才留。
# 另加"通用池": 从对所有域名普遍可用的 CF 任播段随机抽 IP 同样实测, 落点随客户端网络而定,
# ming 可在 V2RayN 里按延迟挑 (低延迟 ≈ 近落点)。
import concurrent.futures, ipaddress, json, random, re, socket, ssl, sys, time, urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "cfpull/3.0"}
TIMEOUT_FETCH = 20
TIMEOUT_PROBE = 6
MAX_PER_COUNTRY = 15
MAX_GENERAL = 30
ZONE_HOST = "cfnew.474101149.xyz"  # 实测目标: ming 自己的 Worker 域名

COUNTRIES = {  # code -> 中文名
    "JP": "日本", "KR": "韩国", "SG": "新加坡", "US": "美国", "GB": "英国", "GL": "通用",
}
SKIP_HOSTS = {"162.159.198.1"}  # 固定占位 IP, 与国家无关

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
        return False
    return any(ip in n for n in CF_NETS)

SOURCES = [
    ("JP", "tiancheng", "https://edt-aio-nav.pages.dev/tiancheng/jp.txt"),
    ("KR", "tiancheng", "https://edt-aio-nav.pages.dev/tiancheng/kr.txt"),
    ("SG", "tiancheng", "https://edt-aio-nav.pages.dev/tiancheng/sg.txt"),
    ("US", "tiancheng", "https://edt-aio-nav.pages.dev/tiancheng/us.txt"),
    ("JP", "gslege", "https://edt-aio-nav.pages.dev/gslege/JP.txt"),
    ("SG", "gslege", "https://edt-aio-nav.pages.dev/gslege/SG.txt"),
    ("US", "gslege", "https://edt-aio-nav.pages.dev/gslege/US.txt"),
]
JZ_FILES = [
    ("jz-best", "https://raw.githubusercontent.com/HandsomeMJZ/cfip/refs/heads/main/best_ips.txt"),
    ("jz-full", "https://raw.githubusercontent.com/HandsomeMJZ/cfip/refs/heads/main/full_ips.txt"),
]

# 通用池抽样段 (对各域名普遍可用的任播段) 与每段抽样数
GEN_POOL = [("104.16.0.0/13", 140), ("172.67.0.0/16", 50),
            ("162.159.192.0/19", 30), ("103.31.4.0/22", 20)]

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

_CTX = ssl.create_default_context()

def zone_probe(item):
    """拿 ZONE_HOST 做 SNI/Host 实测: 返回 HTTP 200 才算这个 IP 真能给 ming 的域名当入口。"""
    cc, host, port, src = item
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_PROBE) as raw:
            with _CTX.wrap_socket(raw, server_hostname=ZONE_HOST) as s:
                s.sendall(f"GET / HTTP/1.1\r\nHost: {ZONE_HOST}\r\nConnection: close\r\nUser-Agent: cfpull/3.0\r\n\r\n".encode())
                data = s.recv(128)
        if data.split(b"\r\n", 1)[0].split()[1:2] == [b"200"]:
            return (cc, host, port, src, round((time.time() - t0) * 1000))
    except Exception:
        pass
    return None

def gen_candidates():
    # 固定种子: 每轮抽同一批候选, 存活集稳定, 避免订阅里的 IP 每 30 分钟全换一遍
    rng = random.Random(20261117)
    out = []
    for cidr, n in GEN_POOL:
        net = ipaddress.ip_network(cidr)
        base, total = int(net.network_address), net.num_addresses
        for off in rng.sample(range(total), min(n, total)):
            out.append(("GL", str(ipaddress.ip_address(base + off)), 443))
    return out

def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    pool = {}
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
    for c2, h, p in gen_candidates():
        pool.setdefault((c2, h, p), "genpool")

    items = [(cc, h, p, s) for (cc, h, p), s in pool.items() if is_stable(h, p)]
    alive = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=60) as ex:
        for r in ex.map(zone_probe, items):
            if r:
                alive.append(r)

    by_cc = {cc: [] for cc in COUNTRIES}
    for cc, h, p, s, ms in alive:
        by_cc[cc].append((ms, h, p, s))
    lines, summary = [], {}
    seen_hp = set()
    for cc in ["JP", "KR", "SG", "US", "GB", "GL"]:
        cap = MAX_GENERAL if cc == "GL" else MAX_PER_COUNTRY
        lst = []
        for entry in sorted(by_cc[cc]):
            hp = (entry[1], entry[2])
            if hp in seen_hp:
                continue
            seen_hp.add(hp)
            lst.append(entry)
            if len(lst) >= cap:
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
