#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Required Notice: Copyright (c) 2026 DawnEcho1254
# Licensed under the MIT License (see LICENSE).
"""
Minecraft 下载器
依赖：pip install ttkbootstrap
仓库：https://github.com/DawnEcho1254/Downloadminercaft
"""

import collections
import concurrent.futures as cf
import hashlib
import io
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.constants import *

# ==================== 常量 ====================

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
BMCLAPI_MANIFEST = "https://bmclapi2.bangbang93.com/mc/game/version_manifest.json"
BMCLAPI_HOST = "bmclapi2.bangbang93.com"
MIRROR_HOSTS = (
    "launchermeta.mojang.com",
    "launcher.mojang.com",
    "piston-meta.mojang.com",
    "piston-data.mojang.com",
    "files.minecraftforge.net",
    "maven.minecraftforge.net",
    "maven.neoforged.net",
)

# ---- 加载器官方源 / BMCLAPI 镜像 ----
# 加载器的官方源是各自的 Maven 仓库，不是 Mojang；Mojang 只提供原版游戏文件。
FABRIC_MAVEN_ROOT = "https://maven.fabricmc.net/net/fabricmc/fabric-installer"
FABRIC_METADATA_URL = FABRIC_MAVEN_ROOT + "/maven-metadata.xml"
# meta.fabricmc.net 只给版本/下载地址的 JSON，不走 maven 主机；
# 国内网络里 maven.fabricmc.net 偶尔 TLS 握手超时，但 meta 通常还能访问。
FABRIC_META_INSTALLER_URLS = (
    "https://meta.fabricmc.net/v2/versions/installer",
    "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/installer",
)
FABRIC_PROFILE_URLS = (
    "https://meta.fabricmc.net/v2/versions/loader/{mc}/{loader}/profile/json",
    "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/loader/{mc}/{loader}/profile/json",
)
QUILT_PROFILE_URL = "https://meta.quiltmc.org/v3/versions/loader/{mc}/{loader}/profile/json"
FABRIC_LOADER_LIST_URLS = (
    "https://meta.fabricmc.net/v2/versions/loader/{mc}",
    "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/loader/{mc}",
)

FORGE_PROMOS_URL = "https://files.minecraftforge.net/maven/net/minecraftforge/forge/promotions_slim.json"
FORGE_OFFICIAL_ROOT = "https://maven.minecraftforge.net/net/minecraftforge/forge"
FORGE_MIRROR_ROOT = "https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge"
FORGE_METADATA_URLS = (
    FORGE_OFFICIAL_ROOT + "/maven-metadata.xml",
    FORGE_MIRROR_ROOT + "/maven-metadata.xml",
)

NEOFORGE_OFFICIAL_ROOT = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
NEOFORGE_MIRROR_ROOT = "https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge"
NEOFORGE_METADATA_URLS = (
    NEOFORGE_MIRROR_ROOT + "/maven-metadata.xml",
    NEOFORGE_OFFICIAL_ROOT + "/maven-metadata.xml",
)

UA = "Downloadminercaft/1.0 (+https://github.com/DawnEcho1254/Downloadminercaft)"

APRIL_FOOLS = {
    "15w14a", "1.RV-Pre1", "3D Shareware v1.34", "20w14infinite",
    "22w13oneBlockAtATime", "23w13a_or_b", "24w14potato", "25w14craftmine",
}

TYPE_ORDER = ["release", "snapshot", "april", "old_alpha", "old_beta"]
TYPE_NAMES = {
    "release": "正式版", "snapshot": "预览版", "april": "愚人节版",
    "old_alpha": "早期 Alpha", "old_beta": "早期 Beta",
}
SOURCE_NAMES = {"mojang": "Mojang 官方", "bmclapi": "BMCLAPI 镜像"}
MIRROR_NAME = "BMCLAPI 镜像"
LOADER_NAMES = {"fabric": "Fabric", "forge": "MinecraftForge", "neoforge": "NeoForged"}
LOADER_OFFICIAL_NAMES = {
    "fabric": "Fabric 官方",
    "forge": "MinecraftForge 官方",
    "neoforge": "NeoForged 官方",
}

SERVER_TABS = [
    ("vanilla", "原版"),
    ("paper", "Paper"),
    ("purpur", "Purpur"),
    ("leaves", "Leaves"),
    ("folia", "Folia"),
]
PROVIDER_NAMES = {p[0]: p[1] for p in SERVER_TABS}

FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")
FONT_BODY = ("Microsoft YaHei UI", 9)
FONT_SMALL = ("Microsoft YaHei UI", 8)
FONT_MONO = ("Consolas", 9)


class DownloadCancelled(Exception):
    pass


# ==================== 通用工具 ====================

def safe_url(url: str) -> str:
    return urllib.parse.quote(url, safe=":/?&=%#[]@!$'()*+,;~")


def http_get(url, timeout=20, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(safe_url(url), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


_HOST_REACHABLE = {}


def _fetch_bytes(url, attempts=2, timeout=20):
    """网络抖动时重试一次，避免整份版本列表因为一次超时而退回滞后镜像。"""
    last_err = None
    for i in range(max(1, attempts)):
        try:
            return http_get(url, timeout=timeout)
        except Exception as e:
            last_err = e
            if i + 1 < attempts:
                time.sleep(0.6)
    raise last_err


def host_reachable(url, timeout=4):
    """探测下载源主机是否可达，结果按主机名缓存。

    maven.neoforged.net 在没有直连线路的网络里会挂起十几秒才报连接被拒，
    如果不先探一下，按“官方优先”准备 NeoForge 下载时每个文件都要先白等一轮。
    """
    host = urllib.parse.urlsplit(url).netloc
    if host in _HOST_REACHABLE:
        return _HOST_REACHABLE[host]
    try:
        req = urllib.request.Request(safe_url(url), headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = int(getattr(resp, "status", 200) or 200) < 500
    except Exception:
        ok = False
    _HOST_REACHABLE[host] = ok
    return ok


def log_exception(context=""):
    """把异常写到 %LOCALAPPDATA%\\mc-downloader-error.log，无 stderr 时也不会二次报错。"""
    try:
        text = "".join(traceback.format_exception(*sys.exc_info()))
    except Exception:
        text = "unknown exception"
    try:
        base = (os.environ.get("LOCALAPPDATA")
                or os.environ.get("TEMP")
                or os.path.expanduser("~"))
        with open(os.path.join(base, "mc-downloader-error.log"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n{text}\n")
    except Exception:
        pass
    if sys.stderr is not None:
        try:
            traceback.print_exc()
        except Exception:
            pass


def hide_console():
    """双击启动时隐藏自动弹出的控制台；从已有终端启动时不动用户的终端。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        processes = (ctypes.c_uint * 8)()
        count = kernel32.GetConsoleProcessList(processes, 8)
        if count and count <= 1:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def apply_window_icon(root):
    """同目录有 mc_downloader.ico 就用作窗口和任务栏图标。"""
    base = os.path.dirname(os.path.abspath(__file__))
    for name in ("mc_downloader.ico", "icon.ico"):
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        try:
            root.iconbitmap(default=path)
            return
        except Exception:
            continue


def human(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "未知"
    if n < 0:
        n = 0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def resolve_url(url, source):
    if source != "bmclapi" or not url:
        return url
    for host in MIRROR_HOSTS:
        url = url.replace(host, BMCLAPI_HOST)
    return url


def source_label(info, prefer=""):
    sources = [SOURCE_NAMES[key] for key in ("mojang", "bmclapi")
               if key in info.get("sources", {})]
    if not sources:
        return "-"
    if prefer == "bmclapi":
        sources.sort(key=lambda x: 0 if x == SOURCE_NAMES["bmclapi"] else 1)
    return " + ".join(sources)


def loader_source_names(loader_mode):
    """加载器的官方源是各自的 Maven 仓库；只有 Forge/NeoForge 有 BMCLAPI 镜像。"""
    official = LOADER_OFFICIAL_NAMES.get(loader_mode, "官方")
    mirror = MIRROR_NAME if loader_mode in ("forge", "neoforge") else None
    return official, mirror


def default_download_dir():
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            try:
                val, _ = winreg.QueryValueEx(
                    key, "{374DE290-123F-4565-9164-39C4925E467B}"
                )
            finally:
                winreg.CloseKey(key)
            val = os.path.expandvars(val)
            if val:
                return val
        except Exception:
            pass
    xdg = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg:
        return os.path.expandvars(xdg)
    home = os.path.expanduser("~")
    for name in ("Downloads", "下载"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            return p
    return os.path.join(home, "Downloads")


# ==================== 辅助函数 ====================

def parse_maven_metadata(xml_text):
    """从 maven-metadata.xml 里取出全部 <version>。返回 [] 表示内容无效。"""
    if not xml_text:
        return []
    versions = [v.strip() for v in re.findall(r"<version>([^<]+)</version>", xml_text)]
    return [v for v in versions if v]


def fetch_maven_versions(urls):
    """按优先级依次尝试仓库地址，返回第一份能解析出内容的版本列表。

    地址顺序已经按“完整优先”排好：Forge 官方在前（BMCLAPI 的 Forge 仓库停在 1.18.2），
    NeoForge 镜像在前（国内直连 maven.neoforged.net 往往会卡住）。前一个失败才试下一个，
    这样既不会拿到滞后镜像的残缺列表，也不会为不可达的域名白等十几秒。
    """
    last_err = None
    for url in urls:
        try:
            versions = parse_maven_metadata(_fetch_bytes(url).decode("utf-8", "ignore"))
        except Exception as e:
            last_err = e
            continue
        if versions:
            return versions
    raise RuntimeError(f"无法获取版本列表：{last_err}")


def parse_forge_promos(data):
    promos = data.get("promos", {})
    result = {}
    for key, ver in promos.items():
        mc_ver, _, kind = key.rpartition("-")
        if not mc_ver:
            continue
        if kind in ("latest", "recommended"):
            result.setdefault(mc_ver, {})[kind] = ver
    return result


# ---- 版本号排序 / 解析 ----

_PRERELEASE_RE = re.compile(
    r"(?:^|[.\-+_])(alpha|beta|rc|pre|preview|snapshot)", re.I
)


def is_prerelease(version):
    return bool(_PRERELEASE_RE.search(str(version)))


def version_tokens(text):
    """把版本号拆成可比较的元组，数字段按数值比，文字段按字符串比，绝不混比 int/str。"""
    tokens = []
    for part in re.split(r"[.\-+_]", str(text)):
        if not part:
            continue
        if part.isdigit():
            tokens.append((0, int(part), ""))
        else:
            tokens.append((1, 0, part.lower()))
    return tokens


def version_key(version):
    """稳定版优先，其次按版本号从大到小排序（reverse=True 时使用）。"""
    return (0 if is_prerelease(version) else 1, version_tokens(version))


def mc_version_key(mc):
    return version_tokens(mc)


def latest_stable(versions):
    for v in versions:
        if not is_prerelease(v):
            return v
    return versions[0] if versions else None


def split_forge_version(full, mc_set):
    """把 Forge 的完整版本号拆成 (MC 版本, Forge 版本)。

    Forge 的完整版本号形如 1.20.1-47.4.28，个别老版本还带后缀（1.7.10-10.13.4.1614-1.7.10、
    1.9.4-12.17.0.2317-1.9.4）。所以优先用已知 MC 版本集合在最靠前的“-”处切分，
    只有集合里找不到时才退回正则。
    """
    idx = full.find("-")
    while idx != -1:
        head = full[:idx]
        if head in mc_set:
            return head, full[idx + 1:]
        idx = full.find("-", idx + 1)
    m = re.match(r"^(\d+\.\d+(?:\.\d+)?)-(.+)$", full)
    if m:
        return m.group(1), m.group(2)
    return None, full


def build_forge_index(versions, promos, mc_set):
    """按 MC 版本归档 Forge 的所有完整版本号，并给出 MC 版本列表（新到旧）。

    只依赖 API 返回是不够的：BMCLAPI 的 Forge 仓库是滞后的，promos 又只有
    latest/recommended。这里把两边合起来，并且直接用完整版本号作为下载 ID，
    避免把 promos 里的裸版本号（47.4.28）当成下载地址。
    """
    index = {}
    for full in versions:
        mc, rest = split_forge_version(full, mc_set)
        if not mc or not rest:
            continue
        index.setdefault(mc, []).append(full)

    for mc, entry in promos.items():
        index.setdefault(mc, [])
        for kind in ("latest", "recommended"):
            ver = entry.get(kind)
            if not ver:
                continue
            full = ver if ver.startswith(mc + "-") else f"{mc}-{ver}"
            if full not in index[mc]:
                index[mc].append(full)

    for mc, items in index.items():
        items.sort(key=version_key, reverse=True)
    mc_versions = sorted(index, key=mc_version_key, reverse=True)
    return index, mc_versions


def neoforge_mc_version(version, mc_set):
    """NeoForge 的版本号不是 MC 版本号前缀（1.21.4 对应 21.4.x，26.3 对应 26.3.x）。

    现代 MC 改成 26.x 命名，所以直接按前缀对应；1.20/1.21 时代则是 1.<major>.<minor>，
    其中 minor=0 表示不带小版本号（21.0.x -> 1.21）。
    """
    m = re.match(r"^(\d+)\.(\d+)", version)
    if not m:
        return None
    major, minor = m.group(1), m.group(2)
    if major == "0":
        return None  # 0.25w14craftmine 这类愚人节快照，不对应正式 MC 版本
    if int(major) <= 21:
        candidates = [f"1.{major}.{minor}"]
        if minor == "0":
            candidates.append(f"1.{major}")
    else:
        candidates = [f"{major}.{minor}"]
    for candidate in candidates:
        if candidate in mc_set:
            return candidate
    return candidates[-1]


def build_neoforge_index(versions, mc_set):
    index = {}
    for version in versions:
        mc = neoforge_mc_version(version, mc_set)
        if not mc:
            continue
        index.setdefault(mc, []).append(version)
    for items in index.values():
        items.sort(key=version_key, reverse=True)
    mc_versions = sorted(index, key=mc_version_key, reverse=True)
    return index, mc_versions


# ==================== 服务端 / 加载器获取 ====================

def _extract_paper_version(item):
    """fill.papermc.io v3 的版本项是 {"version": {"id": "1.21.4", ...}, "builds": [...]}。"""
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return None
    inner = item.get("version")
    if isinstance(inner, str):
        return inner
    if isinstance(inner, dict):
        value = inner.get("id")
        if isinstance(value, str):
            return value
    value = item.get("id")
    return value if isinstance(value, str) else None


def fetch_papermc_versions(project):
    url = f"https://fill.papermc.io/v3/projects/{project}/versions"
    data = json.loads(http_get(url))
    versions_list = data if isinstance(data, list) else (data or {}).get("versions", [])
    out = {}
    for item in versions_list or []:
        version = _extract_paper_version(item)
        if version:
            out[version] = {"provider": project, "version": version}
    return out


def fetch_purpur_versions():
    data = json.loads(http_get("https://api.purpurmc.org/v2/purpur"))
    out = {}
    for ver in (data or {}).get("versions", []):
        if ver:
            out[ver] = {"provider": "purpur", "version": ver}
    return out


def fetch_leaves_versions():
    url = "https://api.github.com/repos/LeavesMC/Leaves/releases?per_page=30"
    data = json.loads(http_get(url))
    out = {}
    for rel in data or []:
        tag = rel.get("tag_name", "")
        if tag:
            out[tag] = {"provider": "leaves", "version": tag}
    return out


SERVER_FETCHERS = {
    "paper": lambda: fetch_papermc_versions("paper"),
    "purpur": fetch_purpur_versions,
    "leaves": fetch_leaves_versions,
    "folia": lambda: fetch_papermc_versions("folia"),
}
PAPERLIKE_PROVIDERS = ("paper", "folia")


def fetch_fabric_installer_versions():
    """优先用 meta JSON 拿版本列表，maven-metadata 只是最后兜底。

    meta.fabricmc.net 和它的 BMCLAPI 镜像都只返回 JSON，比 maven 主机的 XML 更容易访问；
    先走 JSON 能让“列表加载失败”基本不会再出现。
    """
    for url in FABRIC_META_INSTALLER_URLS:
        try:
            data = json.loads(_fetch_bytes(url).decode("utf-8", "ignore"))
        except Exception:
            continue
        versions = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict):
                version = item.get("version")
                if version:
                    versions.append(str(version))
            elif isinstance(item, str):
                versions.append(item)
        if versions:
            return versions
    return fetch_maven_versions((FABRIC_METADATA_URL,))


def fetch_forge_promos():
    data = json.loads(_fetch_bytes(FORGE_PROMOS_URL))
    return parse_forge_promos(data)


def fetch_forge_all_versions():
    return fetch_maven_versions(FORGE_METADATA_URLS)


def fetch_neoforge_all_versions():
    return fetch_maven_versions(NEOFORGE_METADATA_URLS)


# ==================== 整合包解析 ====================

class ModpackError(Exception):
    pass


LOADER_DEPENDENCY_KEYS = {
    "forge": "Forge",
    "neoforge": "NeoForge",
    "fabric-loader": "Fabric",
    "quilt-loader": "Quilt",
}

PACK_DIR_TYPES = {
    "mods": "Mod",
    "config": "配置",
    "defaultconfigs": "默认配置",
    "resourcepacks": "资源包",
    "shaderpacks": "光影",
    "datapacks": "数据包",
    "kubejs": "KubeJS 脚本",
    "scripts": "脚本",
}


def _clean_zip_path(name):
    """压缩包内的路径规范成安全的相对路径；越界/垃圾条目返回 None。"""
    if not name or name.endswith("/"):
        return None
    name = name.replace("\\", "/")
    if name.startswith("/"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    if parts and parts[0] == "__MACOSX":
        return None
    if parts and parts[-1].lower() in (".ds_store", "thumbs.db", "desktop.ini"):
        return None
    return "/".join(parts)


def sanitize_folder_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip(" .")
    return (name or "整合包")[:80]


def modpack_loader_text(pack):
    if pack.get("loader_name"):
        return f"{pack['loader_name']} {pack['loader_version']}".strip()
    return "未声明（整合包未提供加载器信息）"


def _pack_type_for(path):
    head = path.split("/", 1)[0]
    if "/" in path:
        return PACK_DIR_TYPES.get(head, "文件")
    if path.lower().endswith(".jar"):
        return "Mod"
    return "文件"


def _parse_mmc_pack(data):
    """从 MultiMC / Prism 的 mmc-pack.json 里读 MC 与加载器版本。"""
    mc = loader_name = loader_ver = ""
    for component in data.get("components") or []:
        uid = str(component.get("uid", "")).lower()
        version = str(component.get("version", ""))
        if uid == "net.minecraft":
            mc = version
        elif uid == "net.minecraftforge":
            loader_name, loader_ver = "Forge", version
        elif uid == "net.neoforged":
            loader_name, loader_ver = "NeoForge", version
        elif uid == "net.fabricmc.fabric-loader":
            loader_name, loader_ver = "Fabric", version
        elif uid == "org.quiltmc.quilt-loader":
            loader_name, loader_ver = "Quilt", version
    return mc, loader_name, loader_ver


# ---- 生成 PCL / HMCL 能识别的版本 JSON ----

def _json_from_urls(urls, timeout=25):
    last_err = None
    for url in urls:
        try:
            data = json.loads(_fetch_bytes(url, timeout=timeout).decode("utf-8", "ignore"))
        except Exception as e:
            last_err = e
            continue
        if isinstance(data, dict) and data.get("mainClass"):
            return data
    raise RuntimeError(f"获取版本 JSON 失败：{last_err}")


def _version_json_from_installer(urls):
    """Forge / NeoForge 的安装器 jar 里自带 version.json，直接抽出来用。"""
    last_err = None
    for url in urls:
        try:
            blob = _fetch_bytes(url, attempts=1, timeout=90)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                names = set(zf.namelist())
                for member in ("version.json", "install_profile.json"):
                    if member not in names:
                        continue
                    data = json.loads(zf.read(member).decode("utf-8-sig"))
                    if member == "install_profile.json":
                        data = data.get("versionInfo") or data.get("version") or data
                    if isinstance(data, dict) and data.get("mainClass"):
                        return data
        except Exception as e:
            last_err = e
    raise RuntimeError(f"无法从安装器里取出版本 JSON：{last_err}")


def _vanilla_version_json(mc):
    last_err = None
    for manifest_url, source in ((MOJANG_MANIFEST, "mojang"), (BMCLAPI_MANIFEST, "bmclapi")):
        try:
            manifest = json.loads(
                _fetch_bytes(manifest_url, timeout=20).decode("utf-8", "ignore")
            )
            entry = next(
                (v for v in (manifest.get("versions") or []) if v.get("id") == mc), None
            )
            if not entry:
                continue
            data = json.loads(
                _fetch_bytes(resolve_url(entry["url"], source), timeout=25)
                .decode("utf-8", "ignore")
            )
            if isinstance(data, dict) and data.get("mainClass"):
                return data
        except Exception as e:
            last_err = e
    raise RuntimeError(f"无法获取 {mc} 的原版版本 JSON：{last_err}")


def _merge_version_json(base, overlay):
    """把加载器的 profile 合并进原版 JSON，生成一份自包含的完整版本（去掉 inheritsFrom）。

    这样 PCL / HMCL 不需要先装原版或加载器父版本，也不会把它当成“子版本”。
    """
    merged = dict(base)
    merged.pop("inheritsFrom", None)
    if overlay.get("mainClass"):
        merged["mainClass"] = overlay["mainClass"]
    if overlay.get("type"):
        merged["type"] = overlay["type"]

    libraries = list(base.get("libraries") or [])
    seen = {lib.get("name") for lib in libraries if lib.get("name")}
    for lib in overlay.get("libraries") or []:
        name = lib.get("name")
        if name and name in seen:
            continue
        libraries.append(lib)
        if name:
            seen.add(name)
    merged["libraries"] = libraries

    base_args = base.get("arguments") or {}
    over_args = overlay.get("arguments") or {}
    merged_args = {}
    for key in ("game", "jvm"):
        merged_args[key] = list(base_args.get(key) or []) + list(over_args.get(key) or [])
    if merged_args["game"] or merged_args["jvm"]:
        merged["arguments"] = merged_args
    if overlay.get("minecraftArguments") and not base.get("minecraftArguments"):
        merged["minecraftArguments"] = overlay["minecraftArguments"]
    if overlay.get("logging"):
        merged["logging"] = overlay["logging"]
    return merged


def fetch_loader_overlay(mc, loader_name, loader_version):
    """获取加载器版本 profile（只含加载器部分），供合并进已有版本 JSON。"""
    loader = (loader_name or "").lower()
    version = loader_version or ""
    if not mc or not loader or not version:
        raise RuntimeError("缺少 Minecraft 版本或加载器版本")
    if loader == "fabric":
        return _json_from_urls([
            url.format(mc=mc, loader=version) for url in FABRIC_PROFILE_URLS
        ])
    if loader == "quilt":
        return _json_from_urls([
            QUILT_PROFILE_URL.format(mc=mc, loader=version)
        ])
    if loader == "forge":
        vid = version if version.startswith(mc + "-") else f"{mc}-{version}"
        return _version_json_from_installer([
            f"{FORGE_OFFICIAL_ROOT}/{vid}/forge-{vid}-installer.jar",
            f"{FORGE_MIRROR_ROOT}/{vid}/forge-{vid}-installer.jar",
        ])
    if loader == "neoforge":
        vid = version
        return _version_json_from_installer([
            f"{NEOFORGE_MIRROR_ROOT}/{vid}/neoforge-{vid}-installer.jar",
            f"{NEOFORGE_OFFICIAL_ROOT}/{vid}/neoforge-{vid}-installer.jar",
        ])
    raise RuntimeError(f"不支持的加载器：{loader_name}")


def fetch_loader_versions(mc, loader_name):
    """列出某个 Minecraft 版本可用的加载器版本（新到旧）。"""
    loader = (loader_name or "").lower()
    if loader == "fabric":
        for url in FABRIC_LOADER_LIST_URLS:
            try:
                data = json.loads(
                    _fetch_bytes(url.format(mc=mc)).decode("utf-8", "ignore")
                )
            except Exception:
                continue
            versions = []
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict):
                    continue
                inner = item.get("loader")
                version = (inner or {}).get("version") if isinstance(inner, dict) else item.get("version")
                if version:
                    versions.append(str(version))
            if versions:
                return sorted(set(versions), key=version_key, reverse=True)
        raise RuntimeError("无法获取 Fabric 加载器版本")
    if loader == "forge":
        prefixes = (mc + "-",)
        versions = []
        for full in fetch_forge_all_versions():
            for prefix in prefixes:
                if full.startswith(prefix):
                    versions.append(full[len(prefix):])
                    break
        if not versions:
            raise RuntimeError(f"Forge 没有适用于 {mc} 的版本")
        return sorted(set(versions), key=version_key, reverse=True)
    if loader == "neoforge":
        versions = [
            v for v in fetch_neoforge_all_versions()
            if neoforge_mc_version(v, {mc}) == mc
        ]
        if not versions:
            raise RuntimeError(f"NeoForge 没有适用于 {mc} 的版本")
        return sorted(set(versions), key=version_key, reverse=True)
    raise RuntimeError(f"不支持的加载器：{loader_name}")


def build_modpack_version_json(pack):
    """给整合包生成一份完整的、自包含的版本 JSON：加载器的 mainClass / libraries /
    arguments 直接合并进原版清单，不再用 inheritsFrom 挂成子版本。"""
    mc = str(pack.get("mc") or "")
    if not mc:
        raise RuntimeError("整合包没有声明 Minecraft 版本，无法生成版本 JSON")
    base = _vanilla_version_json(mc)
    loader = str(pack.get("loader_name") or "").lower()
    loader_ver = str(pack.get("loader_version") or "")
    if not loader or not loader_ver:
        return base
    overlay = fetch_loader_overlay(mc, loader, loader_ver)
    return _merge_version_json(base, overlay)


QUILT_INSTALLER_ROOT = "https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-installer"


def modpack_loader_installer(pack):
    """整合包对应加载器的安装器：返回文件名和候选下载地址，取不到返回 None。"""
    mc = str(pack.get("mc") or "")
    loader = str(pack.get("loader_name") or "").lower()
    version = str(pack.get("loader_version") or "")
    if not version:
        return None
    if loader == "fabric":
        name = f"fabric-installer-{version}.jar"
        return {
            "loader": "Fabric",
            "filename": name,
            "urls": [f"{FABRIC_MAVEN_ROOT}/{version}/{name}"],
        }
    if loader == "forge":
        vid = version if version.startswith(mc + "-") else f"{mc}-{version}"
        name = f"forge-{vid}-installer.jar"
        return {
            "loader": "Forge",
            "filename": name,
            "urls": [
                f"{FORGE_OFFICIAL_ROOT}/{vid}/{name}",
                f"{FORGE_MIRROR_ROOT}/{vid}/{name}",
            ],
        }
    if loader == "neoforge":
        vid = version
        name = f"neoforge-{vid}-installer.jar"
        return {
            "loader": "NeoForge",
            "filename": name,
            "urls": [
                f"{NEOFORGE_MIRROR_ROOT}/{vid}/{name}",
                f"{NEOFORGE_OFFICIAL_ROOT}/{vid}/{name}",
            ],
        }
    if loader == "quilt":
        name = f"quilt-installer-{version}.jar"
        return {
            "loader": "Quilt",
            "filename": name,
            "urls": [f"{QUILT_INSTALLER_ROOT}/{version}/{name}"],
        }
    return None


def write_modpack_version_json(target, folder_name, data):
    """把版本 JSON 写到整合包目录下：<目录>/<目录名>.json，PCL/HMCL 会把它当成一个版本。"""
    payload = dict(data)
    payload["id"] = folder_name
    path = os.path.join(target, folder_name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def inspect_modpack(path):
    """检查文件是不是整合包，是的话返回它的元数据和文件清单。

    支持：
      - Modrinth .mrpack（或内容相同的 .zip）：modrinth.index.json + overrides/
      - 自带文件的 .zip：mods/、config/ 等目录直接打包，可选 MultiMC 的 mmc-pack.json
    CurseForge 的 manifest.json 需要 CurseForge API 才能解析下载地址，这里明确报错。
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ModpackError("文件不存在")
    if not zipfile.is_zipfile(path):
        raise ModpackError("这不是 .mrpack / .zip 压缩包，无法识别为整合包")

    with zipfile.ZipFile(path) as zf:
        raw_names = [n for n in zf.namelist() if not n.endswith("/")]
        cleaned = {}
        for name in raw_names:
            rel = _clean_zip_path(name)
            if rel:
                cleaned[rel.lower()] = (rel, name)
        if not cleaned:
            raise ModpackError("压缩包里没有任何文件")

        # ---- Modrinth 格式 ----
        if "modrinth.index.json" in cleaned:
            rel, zip_name = cleaned["modrinth.index.json"]
            try:
                index = json.loads(zf.read(zip_name).decode("utf-8-sig"))
            except Exception as e:
                raise ModpackError(f"modrinth.index.json 解析失败：{e}")
            if not isinstance(index, dict):
                raise ModpackError("modrinth.index.json 内容不是对象")
            dependencies = index.get("dependencies") or {}
            mc = str(dependencies.get("minecraft") or "")
            if not mc:
                raise ModpackError("modrinth.index.json 缺少 minecraft 版本")
            loader_name = loader_ver = ""
            for key, label in LOADER_DEPENDENCY_KEYS.items():
                if dependencies.get(key):
                    loader_name, loader_ver = label, str(dependencies[key])
                    break

            files, skipped = [], []
            for item in index.get("files") or []:
                if not isinstance(item, dict):
                    continue
                rel_path = _clean_zip_path(str(item.get("path") or ""))
                urls = [u for u in (item.get("downloads") or [])
                        if isinstance(u, str) and u.startswith(("http://", "https://"))]
                env = item.get("env") or {}
                if not rel_path or not urls:
                    skipped.append(str(item.get("path") or "?"))
                    continue
                if str(env.get("client") or "").lower() == "unsupported":
                    continue
                hashes = item.get("hashes") or {}
                files.append({
                    "path": rel_path,
                    "urls": urls,
                    "size": int(item.get("fileSize") or 0),
                    "sha1": hashes.get("sha1"),
                    "sha512": hashes.get("sha512"),
                })

            local_files = []
            for lower, (rel_path, zip_entry) in cleaned.items():
                for prefix in ("overrides/", "client-overrides/"):
                    if lower.startswith(prefix):
                        target = rel_path[len(prefix):]
                        if target:
                            local_files.append({"path": target, "zip_name": zip_entry})
                        break

            if not files and not local_files:
                raise ModpackError("整合包里既没有可下载文件，也没有 overrides 文件")
            return {
                "kind": "mrpack",
                "path": path,
                "name": str(index.get("name") or os.path.splitext(os.path.basename(path))[0]),
                "version": str(index.get("versionId") or ""),
                "summary": str(index.get("summary") or ""),
                "mc": mc,
                "loader_name": loader_name,
                "loader_version": loader_ver,
                "files": files,
                "local_files": local_files,
                "skipped": skipped,
            }

        # ---- CurseForge 格式：明确说明不支持的原因 ----
        if "manifest.json" in cleaned:
            rel, zip_name = cleaned["manifest.json"]
            try:
                manifest = json.loads(zf.read(zip_name).decode("utf-8-sig"))
            except Exception:
                manifest = {}
            minecraft = manifest.get("minecraft") if isinstance(manifest, dict) else None
            if minecraft and minecraft.get("modLoaders"):
                raise ModpackError(
                    "这是 CurseForge 格式的整合包：Mod 的下载地址要调用 CurseForge API，"
                    "没有 API Key 无法自动下载，请改用 Modrinth 的 .mrpack"
                )

        # ---- 自带文件的 zip ----
        names = [rel for rel, _ in cleaned.values()]
        lower_names = [rel.lower() for rel in names]

        def strip_prefix(prefix):
            prefix = prefix.lower()
            out = []
            for lower, (rel, zip_entry) in cleaned.items():
                if not lower.startswith(prefix):
                    return None
                target = rel[len(prefix):]
                if target:
                    out.append({"path": target, "zip_name": zip_entry})
            return out or None

        local_files = None
        if all(lower.startswith(".minecraft/") for lower in lower_names):
            local_files = strip_prefix(".minecraft/")
        if local_files is None:
            heads = {rel.split("/", 1)[0] for rel in names if "/" in rel}
            if len(heads) == 1:
                head = next(iter(heads))
                if head.lower() not in PACK_DIR_TYPES and any(
                    lower.startswith(head.lower() + "/mods/")
                    or lower.startswith(head.lower() + "/config/")
                    for lower in lower_names
                ):
                    local_files = strip_prefix(head + "/")
        if local_files is None:
            local_files = [{"path": rel, "zip_name": zip_entry}
                           for rel, zip_entry in cleaned.values()]

        has_pack_dir = any(
            item["path"].split("/", 1)[0].lower() in PACK_DIR_TYPES
            for item in local_files if "/" in item["path"]
        )
        if not has_pack_dir:
            raise ModpackError(
                "压缩包里没有 modrinth.index.json，也没有 mods/、config/ 等整合包目录，"
                "无法识别为整合包"
            )

        mc = loader_name = loader_ver = ""
        mmc_entry = None
        for lower, (rel, zip_entry) in cleaned.items():
            if rel.split("/")[-1].lower() == "mmc-pack.json":
                mmc_entry = zip_entry
                break
        if mmc_entry:
            try:
                mc, loader_name, loader_ver = _parse_mmc_pack(
                    json.loads(zf.read(mmc_entry).decode("utf-8-sig"))
                )
            except Exception:
                pass

        return {
            "kind": "zip",
            "path": path,
            "name": os.path.splitext(os.path.basename(path))[0],
            "version": "",
            "summary": "",
            "mc": mc,
            "loader_name": loader_name,
            "loader_version": loader_ver,
            "files": [],
            "local_files": local_files,
            "skipped": [],
        }


# ==================== Windows 拖放 ====================

class FileDropTarget:
    """用 Windows 原生的 WM_DROPFILES 接收拖入的文件，不依赖第三方库。"""

    WM_DROPFILES = 0x0233
    GWLP_WNDPROC = -4

    def __init__(self, root, on_files):
        self.root = root
        self.on_files = on_files
        self.hwnd = None
        self._hooks = {}
        self._user32 = None
        self._shell32 = None
        self._ctypes = None
        if os.name != "nt":
            return
        try:
            self._install()
        except Exception:
            self._hooks = {}
            self.hwnd = None

    def _install(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        self._ctypes = ctypes
        self._user32 = user32
        self._shell32 = shell32
        proto = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_void_p
        ]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        ]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryFileW.argtypes = [
            wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT
        ]

        child = self.root.winfo_id()
        parent = user32.GetParent(child) or child
        for hwnd in {child, parent}:
            proc = proto(self._handle)
            old = user32.SetWindowLongPtrW(
                hwnd, self.GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p)
            )
            if old:
                self._hooks[hwnd] = (old, proc)
                shell32.DragAcceptFiles(hwnd, True)
        if not self._hooks:
            raise OSError("无法接管窗口的消息循环")
        self.hwnd = child

    def _handle(self, hwnd, msg, wparam, lparam):
        try:
            if msg == self.WM_DROPFILES:
                files = self._query_files(wparam)
                if files:
                    self.root.after(0, lambda: self._deliver(files))
                return 0
            hook = self._hooks.get(hwnd)
            if hook:
                return self._user32.CallWindowProcW(
                    hook[0], hwnd, msg, wparam, lparam
                )
        except Exception:
            pass
        return 0

    def _query_files(self, hdrop):
        count = self._shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        files = []
        for index in range(count):
            length = self._shell32.DragQueryFileW(hdrop, index, None, 0) + 1
            buffer = self._ctypes.create_unicode_buffer(length)
            self._shell32.DragQueryFileW(hdrop, index, buffer, length)
            if buffer.value:
                files.append(buffer.value)
        self._shell32.DragFinish(hdrop)
        return files

    def _deliver(self, files):
        try:
            self.on_files(files)
        except Exception:
            log_exception("file_drop")

    def close(self):
        for hwnd, (old, _proc) in list(self._hooks.items()):
            try:
                self._user32.SetWindowLongPtrW(
                    hwnd, self.GWLP_WNDPROC, self._ctypes.cast(old, self._ctypes.c_void_p)
                )
                self._shell32.DragAcceptFiles(hwnd, False)
            except Exception:
                pass
        self._hooks = {}


# ==================== 主应用 ====================

class MCDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft 下载器")
        self.root.geometry("1280x1000")
        self.root.minsize(1100, 800)

        self.mode = "client"
        self.dark_mode = False

        self.versions = {}
        self.client_checked = set()
        self.client_collapsed = set()

        self.server_checked = {}
        self.server_collapsed = {}
        self.server_third = {}
        self.server_data_loaded = False
        self._server_loading = False
        self._manifests_loading = False
        self._fabric_loading = False

        self.loader_mode = "fabric"
        self.fabric_versions = []
        self.fabric_checked = set()
        self.fabric_ext = "jar"
        self.fabric_ext_var = tk.StringVar(value="jar")
        self.fabric_error = None

        self.forge_promos = {}
        self.forge_index = {}
        self.forge_mc_versions = []
        self.forge_loaded = False
        self._forge_loading = False
        self.current_forge_mc = None
        self.forge_checked = set()

        self.neoforge_index = {}
        self.neoforge_mc_versions = []
        self.neoforge_loaded = False
        self._neoforge_loading = False
        self.neoforge_checked = set()

        self.modpack = None
        self.modpack_path = ""
        self.modpack_files = {}
        self.modpack_inspecting = False
        self.modpack_failures = 0
        self.modpack_finished = False
        self._modpack_installing = False
        self._version_json_path = None
        self._version_json_error = None
        self._modpack_extra_vids = set()
        self._modpack_extra_failures = 0

        self.priority_source = "mojang"

        self.task_widgets = {}
        self.task_got = {}
        self.total_size = 0
        self.queue = queue.Queue()
        self.download_thread = None
        self._search_after = None

        # 多批次下载调度
        self.download_queue = collections.deque()
        self.download_lock = threading.Lock()
        self.active_workers = 0
        self._outstanding = 0
        self._preparing = 0
        self._target_workers = 4
        self._active_vids = set()
        self.downloading = False
        self._modpack_remaining = 0
        self._modpack_vids = set()

        self.stop_event = threading.Event()
        self._closing = False
        self._poll_job = None

        self.out_dir_var = tk.StringVar(value=default_download_dir())
        self.mc_dir_var = tk.StringVar(value="")
        self.installed_versions = []
        self.installed_selected = None
        self.installed_mc = ""
        self._installed_loader = ""
        self._loader_req = 0
        self._installer_busy = False
        self.concurrency_var = tk.StringVar(value="4")
        # 每个模式记住自己的并发数：整合包默认 16 线程，其它模式默认 4
        self.concurrency_by_mode = {
            "client": "4", "server": "4", "loader": "4", "modpack": "16",
        }
        self._concurrency_switching = False
        self.concurrency_var.trace_add("write", self._remember_concurrency)
        self.total_progress_var = tk.DoubleVar(value=0.0)
        self.server_provider_var = tk.StringVar(value="vanilla")
        self.loader_provider_var = tk.StringVar(value="fabric")

        self.build_ui()
        self.file_drop = FileDropTarget(self.root, self.on_files_dropped)
        self.refresh_filetree()
        self.load_client_manifests()
        self.poll_queue()

    # ---------- 界面 ----------

    def build_ui(self):
        header = tb.Frame(self.root, padding=(16, 12, 16, 4))
        header.pack(fill=X)
        tb.Label(
            header, text="Minecraft 下载器",
            font=("Microsoft YaHei UI", 16, "bold"), bootstyle=PRIMARY,
        ).pack(side=LEFT)

        mode_frame = tb.Frame(header)
        mode_frame.pack(side=RIGHT)
        self.mode_var = tk.StringVar(value="client")
        # 客户端 / 整合包 / 安装加载器 一组；服务端 / 下载加载器 一组
        for group_index, group in enumerate((
            (("客户端", "client"), ("整合包", "modpack"), ("安装加载器", "installer")),
            (("服务端", "server"), ("加载器", "loader")),
        )):
            if group_index:
                tk.Frame(
                    mode_frame, width=1, bg=self._divider_color()
                ).pack(side=LEFT, fill=Y, padx=8, pady=4)
            for text, val in group:
                tb.Radiobutton(
                    mode_frame, text=text, variable=self.mode_var, value=val,
                    command=self.on_mode_change, bootstyle="info-toolbutton",
                ).pack(side=LEFT, padx=(0, 2))

        self.theme_btn = tb.Button(
            header, text="🌙 暗色", command=self.toggle_theme,
            bootstyle="secondary-outline", width=8,
        )
        self.theme_btn.pack(side=RIGHT, padx=(0, 10))

        # 窗口最底部只留退出按钮；总进度放进右下角的进度区
        self.exit_bar = tb.Frame(self.root, padding=(16, 2, 16, 6))
        self.exit_bar.pack(side=BOTTOM, fill=X)
        tb.Button(
            self.exit_bar, text="退出", command=self.on_close,
            bootstyle="danger-outline", width=10,
        ).pack(side=RIGHT)

        self.search_card = tb.Frame(self.root, padding=(16, 4, 16, 4))
        self.search_card.pack(fill=X)
        self.search_frame = tb.Frame(self.search_card)
        sf = self.search_frame
        sf.pack(fill=X)
        tb.Label(sf, text="搜索", font=FONT_BODY, bootstyle=SECONDARY).pack(
            side=LEFT, padx=(10, 6), pady=4
        )
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.on_search_change)
        self.search_entry = tb.Entry(sf, textvariable=self.search_var, font=FONT_BODY)
        self.search_entry.pack(side=LEFT, fill=X, expand=True, padx=4, pady=4)
        tb.Button(
            sf, text="清空", command=lambda: self.search_var.set(""),
            bootstyle="secondary-outline", width=6,
        ).pack(side=LEFT, padx=(0, 8), pady=4)

        # 中间区：上 1/2 是版本列表，下 1/2 左右分栏（左下控件+文件树，右下进度）
        self.middle_frame = tb.Frame(self.root)
        self.middle_frame.pack(fill=BOTH, expand=True)

        self.top_half = tb.Frame(self.middle_frame)
        self.top_half.place(x=0, y=0, relwidth=1, relheight=0.5)
        self.bottom_half = tb.Frame(self.middle_frame)
        self.bottom_half.place(x=0, rely=0.5, relwidth=1, relheight=0.5)

        self.left_col = tb.Frame(self.bottom_half, padding=(16, 4, 8, 8))
        self.left_col.place(x=0, y=0, relwidth=1 / 3, relheight=1)
        self.right_col = tb.Frame(self.bottom_half, padding=(8, 4, 16, 8))
        self.right_col.place(relx=1 / 3, y=0, relwidth=2 / 3, relheight=1)

        self.server_tabs_frame = tb.Frame(self.top_half, padding=(16, 4, 16, 2))
        for pid, pname in SERVER_TABS:
            tb.Radiobutton(
                self.server_tabs_frame, text=pname,
                variable=self.server_provider_var, value=pid,
                command=self.on_server_provider_change,
                bootstyle="secondary-toolbutton",
            ).pack(side=LEFT, padx=(0, 4))

        self.loader_tabs_frame = tb.Frame(self.top_half, padding=(16, 4, 16, 2))
        for lname, lval in [("Fabric", "fabric"), ("Forge", "forge"), ("NeoForge", "neoforge")]:
            tb.Radiobutton(
                self.loader_tabs_frame, text=lname,
                variable=self.loader_provider_var, value=lval,
                command=self.on_loader_provider_change,
                bootstyle="secondary-toolbutton",
            ).pack(side=LEFT, padx=(0, 4))

        self.list_card = tb.Labelframe(
            self.top_half, text=" 主工作区 ", padding=8, bootstyle="info"
        )
        self.list_card.pack(fill=BOTH, expand=True, padx=16, pady=(4, 4))

        toolbar = tb.Frame(self.list_card)
        toolbar.pack(fill=X)
        self.select_all_btn = tb.Button(
            toolbar, text="全选", command=lambda: self.select_all(True),
            bootstyle="secondary-outline", width=6,
        )
        self.select_all_btn.pack(side=LEFT)
        self.select_none_btn = tb.Button(
            toolbar, text="全不选", command=lambda: self.select_all(False),
            bootstyle="secondary-outline", width=6,
        )
        self.select_none_btn.pack(side=LEFT, padx=(4, 0))
        self.refresh_btn = tb.Button(
            toolbar, text="刷新", command=self.refresh_version_list,
            bootstyle="info-outline", width=6,
        )
        self.refresh_btn.pack(side=LEFT, padx=(4, 0))
        self.count_label = tb.Label(
            toolbar, text="已选：0", font=FONT_SMALL, bootstyle=SECONDARY
        )
        self.count_label.pack(side=RIGHT)

        self.list_body_container = tb.Frame(self.list_card)
        self.list_body_container.pack(fill=BOTH, expand=True, pady=(6, 0))

        # 单列表模式
        self.single_list_frame = tb.Frame(self.list_body_container)
        self.single_list_frame.pack(fill=BOTH, expand=True)
        
        self.tree = tb.Treeview(
            self.single_list_frame, columns=("id", "date", "source"),
            show="tree headings", height=8,
        )
        self.tree.heading("#0", text="版本 / 加载器")
        self.tree.heading("id", text="")
        self.tree.heading("date", text="发布日期")
        self.tree.heading("source", text="来源")
        self.tree.column("#0", width=800, stretch=False)
        self.tree.column("id", width=0, minwidth=0, stretch=False)
        self.tree.column("date", width=200, anchor=W, stretch=False)
        self.tree.column("source", width=200, anchor=W, stretch=False)
        tb.Style().configure("Treeview", rowheight=24)
        scroll = tb.Scrollbar(self.single_list_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)
        self.tree.bind("<Button-1>", self.on_tree_press)
        self._setup_column_dividers()

        # 三列模式 (Frame 布局，避免 PanedWindow 兼容问题)
        self.forge_three_col_frame = tb.Frame(self.list_body_container)
        
        left_col = tb.Frame(self.forge_three_col_frame)
        left_col.pack(side=LEFT, fill=BOTH, expand=True)
        tb.Label(left_col, text="MC 版本", font=FONT_BODY, bootstyle=SECONDARY).pack(anchor=W, pady=(0, 4))
        self.col_mc_tree = tb.Treeview(left_col, columns=("mc",), show="tree")
        self.col_mc_tree.pack(fill=BOTH, expand=True)
        self.col_mc_tree.bind("<<TreeviewSelect>>", self.on_mc_select)
        
        tb.Frame(self.forge_three_col_frame, width=2, bootstyle="secondary").pack(side=LEFT, fill=Y, padx=2)
        
        mid_col = tb.Frame(self.forge_three_col_frame)
        mid_col.pack(side=LEFT, fill=BOTH, expand=True)
        tb.Label(mid_col, text="推荐 / 最新", font=FONT_BODY, bootstyle=SECONDARY).pack(anchor=W, pady=(0, 4))
        self.col_main_tree = tb.Treeview(mid_col, columns=("ver",), show="tree")
        self.col_main_tree.pack(fill=BOTH, expand=True)
        self.col_main_tree.bind("<Button-1>", lambda e: self.on_three_col_press(e, self.col_main_tree))
        
        tb.Frame(self.forge_three_col_frame, width=2, bootstyle="secondary").pack(side=LEFT, fill=Y, padx=2)
        
        right_col = tb.Frame(self.forge_three_col_frame)
        right_col.pack(side=LEFT, fill=BOTH, expand=True)
        tb.Label(right_col, text="所有版本", font=FONT_BODY, bootstyle=SECONDARY).pack(anchor=W, pady=(0, 4))
        self.col_all_tree = tb.Treeview(right_col, columns=("ver",), show="tree")
        self.col_all_tree.pack(fill=BOTH, expand=True)
        self.col_all_tree.bind("<Button-1>", lambda e: self.on_three_col_press(e, self.col_all_tree))

        # 整合包模式
        self.modpack_frame = tb.Frame(self.list_body_container)

        pack_toolbar = tb.Frame(self.modpack_frame)
        pack_toolbar.pack(fill=X)
        tb.Button(
            pack_toolbar, text="选择整合包", command=self.choose_modpack,
            bootstyle="info-outline", width=12,
        ).pack(side=LEFT)
        tb.Button(
            pack_toolbar, text="清空", command=self.clear_modpack,
            bootstyle="secondary-outline", width=6,
        ).pack(side=LEFT, padx=(6, 0))
        self.pack_info_label = tb.Label(
            pack_toolbar, text="未选择整合包", font=FONT_SMALL, bootstyle=SECONDARY
        )
        self.pack_info_label.pack(side=RIGHT)

        # 状态/提示行放在拖放框上方：这里高度稳定，不会被文件清单和拖放框挤掉
        self.pack_status = tb.Label(
            self.modpack_frame, text="支持 Modrinth .mrpack，以及带 mods/、config/ 的 .zip",
            font=FONT_SMALL, bootstyle=SECONDARY, anchor=W,
        )
        self.pack_status.pack(fill=X, pady=(4, 0))

        self.drop_zone = tk.Frame(
            self.modpack_frame, bd=0, height=72,
            highlightthickness=2, highlightbackground=self._divider_color(),
            bg=self._bg_color(),
        )
        self.drop_zone.pack(fill=X, pady=(6, 6))
        self.drop_zone.pack_propagate(False)
        self.drop_label = tb.Label(
            self.drop_zone, text="把 .mrpack / .zip 拖到这里",
            font=FONT_TITLE, bootstyle=PRIMARY, anchor=CENTER, cursor="hand2",
        )
        self.drop_label.pack(fill=X, pady=(8, 0))
        self.drop_hint = tb.Label(
            self.drop_zone,
            text="也可以点击这里或上面的“选择整合包”，会自动识别是不是整合包",
            font=FONT_SMALL, bootstyle=SECONDARY, anchor=CENTER, cursor="hand2",
        )
        self.drop_hint.pack(fill=X, pady=(2, 6))
        for widget in (self.drop_zone, self.drop_label, self.drop_hint):
            widget.bind("<Button-1>", lambda event: self.choose_modpack())

        pack_body = tb.Frame(self.modpack_frame)
        pack_body.pack(fill=BOTH, expand=True)
        self.pack_tree = tb.Treeview(
            pack_body, columns=("size", "type", "source"),
            show="tree headings", height=5,
        )
        self.pack_tree.heading("#0", text="文件 / 目录")
        self.pack_tree.heading("size", text="大小")
        self.pack_tree.heading("type", text="类型")
        self.pack_tree.heading("source", text="来源")
        self.pack_tree.column("#0", width=620, stretch=True)
        self.pack_tree.column("size", width=110, anchor=E, stretch=False)
        self.pack_tree.column("type", width=120, anchor=W, stretch=False)
        self.pack_tree.column("source", width=180, anchor=W, stretch=False)
        pack_scroll = tb.Scrollbar(pack_body, orient=VERTICAL, command=self.pack_tree.yview)
        self.pack_tree.configure(yscrollcommand=pack_scroll.set)
        self.pack_tree.pack(side=LEFT, fill=BOTH, expand=True)
        pack_scroll.pack(side=RIGHT, fill=Y)

        # 安装加载器
        self.installer_frame = tb.Frame(self.list_body_container)

        path_row = tb.Frame(self.installer_frame)
        path_row.pack(fill=X)
        tb.Label(path_row, text="游戏路径", font=FONT_BODY, bootstyle=SECONDARY).pack(
            side=LEFT, padx=(0, 8)
        )
        self.mc_dir_entry = tb.Entry(
            path_row, textvariable=self.mc_dir_var, font=FONT_MONO
        )
        self.mc_dir_entry.pack(side=LEFT, fill=X, expand=True)
        self.mc_dir_entry.bind("<Return>", lambda e: self.scan_installed_versions())
        tb.Button(
            path_row, text="浏览", command=self.choose_mc_dir,
            bootstyle="secondary-outline", width=6,
        ).pack(side=LEFT, padx=(6, 0))
        tb.Button(
            path_row, text="刷新", command=self.scan_installed_versions,
            bootstyle="info-outline", width=6,
        ).pack(side=LEFT, padx=(4, 0))

        self.installer_hint = tb.Label(
            self.installer_frame,
            text="请选择或输入 .minecraft 文件夹（其中应包含 versions 子目录）",
            font=FONT_SMALL, bootstyle=SECONDARY, anchor=W,
        )
        self.installer_hint.pack(fill=X, pady=(4, 0))

        self.installed_card = tb.Labelframe(
            self.installer_frame, text=" 存有的游戏版本 ", padding=8, bootstyle="info"
        )
        self.installed_card.pack(fill=BOTH, expand=True, pady=(6, 4))
        self.installed_tree = tb.Treeview(
            self.installed_card, show="tree", selectmode="browse", height=6,
        )
        self.installed_tree.column("#0", width=400)
        installed_scroll = tb.Scrollbar(
            self.installed_card, orient=VERTICAL, command=self.installed_tree.yview
        )
        self.installed_tree.configure(yscrollcommand=installed_scroll.set)
        self.installed_tree.pack(side=LEFT, fill=BOTH, expand=True)
        installed_scroll.pack(side=RIGHT, fill=Y)
        self.installed_tree.bind("<<TreeviewSelect>>", self.on_installed_version_select)

        self.installer_options = tb.Labelframe(
            self.installer_frame, text=" 安装器选项 ", padding=8, bootstyle="secondary"
        )
        self.installer_options.pack(fill=X, pady=(4, 0))
        self.installed_path_label = tb.Label(
            self.installer_options, text="未选择游戏版本", font=FONT_SMALL,
            bootstyle=SECONDARY, anchor=W, wraplength=900,
        )
        self.installed_path_label.pack(fill=X)
        self.game_version_label = tb.Label(
            self.installer_options, text="游戏版本：-", font=FONT_SMALL,
            bootstyle=SECONDARY, anchor=W,
        )
        self.game_version_label.pack(fill=X, pady=(2, 6))

        loader_row = tb.Frame(self.installer_options)
        loader_row.pack(fill=X)
        tb.Label(loader_row, text="加载器", font=FONT_BODY, bootstyle=SECONDARY).pack(
            side=LEFT, padx=(0, 8)
        )
        self.installer_loader_var = tk.StringVar(value="")
        self.installer_loader_combo = tb.Combobox(
            loader_row, textvariable=self.installer_loader_var,
            values=("Fabric", "Forge", "NeoForge"), state=DISABLED, width=16,
        )
        self.installer_loader_combo.pack(side=LEFT)
        self.installer_loader_combo.bind(
            "<<ComboboxSelected>>", lambda e: self.on_installer_loader_change()
        )

        version_row = tb.Frame(self.installer_options)
        version_row.pack(fill=X, pady=(6, 0))
        tb.Label(version_row, text="加载器版本", font=FONT_BODY, bootstyle=SECONDARY).pack(
            side=LEFT, padx=(0, 8)
        )
        self.installer_loader_version_var = tk.StringVar(value="")
        self.installer_version_combo = tb.Combobox(
            version_row, textvariable=self.installer_loader_version_var,
            values=(), state=DISABLED, width=24,
        )
        self.installer_version_combo.pack(side=LEFT, fill=X, expand=True)
        self.installer_version_combo.bind(
            "<<ComboboxSelected>>", lambda e: self.on_installer_version_change()
        )

        self.installer_btn = tb.Button(
            self.installer_options, text="安装", command=self.install_selected_loader,
            bootstyle=SUCCESS, state=DISABLED, width=10,
        )
        self.installer_btn.pack(anchor=E, pady=(8, 0))
        self.installer_status = tb.Label(
            self.installer_options, text="", font=FONT_SMALL, bootstyle=SECONDARY, anchor=W,
        )
        self.installer_status.pack(fill=X, pady=(4, 0))

        self.loader_sub_frame = tb.Frame(self.top_half, padding=(16, 4, 16, 4))

        self.pri_card = tb.Frame(self.top_half, padding=(16, 4, 16, 4))
        self.pri_card.pack(fill=X)
        self.priority_btn = tb.Button(
            self.pri_card, text="", command=self.toggle_priority,
            bootstyle="info-outline", width=52,
        )
        self.priority_btn.pack(side=LEFT)
        self.update_priority_btn()

        self.path_card = tb.Labelframe(
            self.left_col, text=" 下载控件 ", padding=8, bootstyle="success"
        )
        self.path_card.pack(fill=X)

        # 第一行：下载路径
        pf = tb.Frame(self.path_card)
        pf.pack(fill=X)
        tb.Label(pf, text="下载路径", font=FONT_BODY, bootstyle=SECONDARY).pack(side=LEFT)
        self.path_entry = tb.Entry(pf, textvariable=self.out_dir_var, font=FONT_MONO)
        self.path_entry.pack(side=LEFT, fill=X, expand=True, padx=(8, 6))
        self.path_entry.bind("<Button-1>", lambda e: self.choose_dir())
        tb.Button(
            pf, text="浏览", command=self.choose_dir, bootstyle="secondary-outline", width=6
        ).pack(side=LEFT)

        # 第二行：并发数 + 开始/停止
        pf2 = tb.Frame(self.path_card)
        pf2.pack(fill=X, pady=(4, 0))
        tb.Label(pf2, text="并发", font=FONT_BODY, bootstyle=SECONDARY).pack(side=LEFT)
        self.concurrency_spin = tb.Spinbox(
            pf2, from_=1, to=32, width=3, textvariable=self.concurrency_var, font=FONT_MONO
        )
        self.concurrency_spin.pack(side=LEFT, padx=(6, 10))
        self.download_btn = tb.Button(
            pf2, text="开始下载", command=self.start_download,
            bootstyle=SUCCESS, state=DISABLED, width=10,
        )
        self.download_btn.pack(side=RIGHT, padx=(6, 0))
        self.stop_btn = tb.Button(
            pf2, text="停止下载", command=self.stop_download,
            bootstyle="danger-outline", state=DISABLED, width=10,
        )
        self.stop_btn.pack(side=RIGHT)

        tree_card = tb.Labelframe(
            self.left_col, text=" 下载目录文件树 ", padding=8, bootstyle="secondary"
        )
        tree_card.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.filetree = tb.Treeview(
            tree_card, columns=("size",), show="tree headings", height=4,
        )
        self.filetree.heading("#0", text="文件 / 目录")
        self.filetree.heading("size", text="大小")
        self.filetree.column("#0", width=300)
        self.filetree.column("size", width=100, anchor=E)
        ft_scroll = tb.Scrollbar(tree_card, orient=VERTICAL, command=self.filetree.yview)
        self.filetree.configure(yscrollcommand=ft_scroll.set)
        self.filetree.pack(side=LEFT, fill=BOTH, expand=True)
        ft_scroll.pack(side=RIGHT, fill=Y)

        # 右下：下载进度（上） + 总进度（下）
        prog_card = tb.Labelframe(
            self.right_col, text=" 下载进度 ", padding=8, bootstyle="primary"
        )
        self.progress_canvas = tk.Canvas(
            prog_card, highlightthickness=0, height=180, bg=self._bg_color()
        )
        progress_scroll = tb.Scrollbar(
            prog_card, orient=VERTICAL, command=self.progress_canvas.yview
        )
        self.progress_inner = tb.Frame(self.progress_canvas)
        self.progress_inner.bind(
            "<Configure>",
            lambda e: self.progress_canvas.configure(
                scrollregion=self.progress_canvas.bbox("all")
            ),
        )
        self.progress_window = self.progress_canvas.create_window(
            (0, 0), window=self.progress_inner, anchor=NW
        )
        self.progress_canvas.configure(yscrollcommand=progress_scroll.set)
        self.progress_canvas.bind(
            "<Configure>",
            lambda e: self.progress_canvas.itemconfigure(
                self.progress_window, width=e.width
            ),
        )
        self.progress_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        progress_scroll.pack(side=RIGHT, fill=Y)
        self.progress_canvas.bind("<MouseWheel>", self._scroll_progress)
        self.progress_inner.bind("<MouseWheel>", self._scroll_progress)

        total_card = tb.Frame(self.right_col, padding=(0, 4, 0, 0))
        total_card.pack(side=BOTTOM, fill=X)
        tf = tb.Frame(total_card)
        tf.pack(fill=X)
        tb.Label(tf, text="总进度", font=FONT_TITLE, bootstyle=PRIMARY).pack(side=LEFT)
        self.total_progress = tb.Progressbar(
            tf, variable=self.total_progress_var, maximum=100,
            bootstyle="success-striped",
        )
        self.total_progress.pack(side=LEFT, fill=X, expand=True, padx=(10, 10))
        self.total_label = tb.Label(
            tf, text="0%", width=30, font=FONT_MONO, bootstyle=SECONDARY
        )
        self.total_label.pack(side=LEFT)
        prog_card.pack(fill=BOTH, expand=True)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _bg_color(self):
        try:
            return self.root.style.colors.bg
        except Exception:
            return "#ffffff"

    def _scroll_progress(self, event):
        step = -1 if getattr(event, "delta", 0) > 0 else 1
        self.progress_canvas.yview_scroll(step * 2, "units")
        return "break"

    # ---------- 主题切换 ----------

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        theme = "darkly" if self.dark_mode else "cosmo"
        try:
            self.root.style.theme_use(theme)
        except Exception:
            log_exception("toggle_theme")
            self.dark_mode = not self.dark_mode
            return
        self.theme_btn.configure(text="☀ 亮色" if self.dark_mode else "🌙 暗色")
        self._refresh_theme_colors()

    def _refresh_theme_colors(self):
        """换主题后，tk 原生控件（Canvas / Frame）不会自动换色，这里手动同步。"""
        bg = self._bg_color()
        border = self._divider_color()
        try:
            self.progress_canvas.configure(bg=bg)
        except Exception:
            pass
        try:
            self.drop_zone.configure(bg=bg, highlightbackground=border)
        except Exception:
            pass
        for line in getattr(self, "column_dividers", []):
            try:
                line.configure(bg=border)
            except Exception:
                pass
        try:
            tb.Style().configure("Treeview", rowheight=24)
        except Exception:
            pass

    # ---------- 列表分隔线 ----------

    def _divider_color(self):
        try:
            return self.root.style.colors.border
        except Exception:
            return "#c9c9c9"

    def _setup_column_dividers(self):
        """Treeview 本身没有网格线，用 1px 的 Frame 贴在列边界上当分割线。"""
        self.column_dividers = []
        self._divider_job = None
        for _ in range(2):
            line = tk.Frame(
                self.tree, width=1, bd=0, highlightthickness=0,
                background=self._divider_color(),
            )
            self.column_dividers.append(line)
        # 窗口一变化，ttk 会先调整列宽再重画，等它画完（after）再取 bbox，位置才准
        self.tree.bind("<Configure>", self._schedule_column_dividers, add="+")
        self.root.after(150, self._refresh_column_layout)

    def _schedule_column_dividers(self, event=None):
        if getattr(self, "_closing", False):
            return
        if self._divider_job:
            try:
                self.root.after_cancel(self._divider_job)
            except Exception:
                pass
        self._divider_job = self.root.after(30, self._refresh_column_layout)

    def _refresh_column_layout(self):
        self._divider_job = None
        self._layout_tree_columns()
        self._place_column_dividers()

    def _layout_tree_columns(self):
        """版本列占 2/3，发布日期与来源各占 1/6。"""
        tree = self.tree
        total = tree.winfo_width()
        if total <= 120:
            return
        # 留 2px 给控件边框，避免最后多出横向滚动条
        available = total - 2
        version_w = int(available * 2 / 3)
        date_w = int(available / 6)
        source_w = available - version_w - date_w
        if (int(tree.column("#0", "width")) == version_w
                and int(tree.column("date", "width")) == date_w):
            return
        tree.column("#0", width=version_w, stretch=False)
        tree.column("date", width=date_w, stretch=False)
        tree.column("source", width=source_w, stretch=False)

    def _column_x(self, tree, column):
        """用可见行的单元格几何拿到列起点，这比按列宽推算少掉边框误差。"""
        for item in tree.get_children(""):
            box = tree.bbox(item, column)
            if box:
                return box[0]
        return None

    def _place_column_dividers(self, event=None):
        if getattr(self, "_closing", False):
            return
        dividers = getattr(self, "column_dividers", None)
        if not dividers:
            return
        tree = self.tree
        total = tree.winfo_width()
        height = tree.winfo_height()
        if total <= 1 or height <= 1:
            self.root.after(100, self._place_column_dividers)
            return
        x_date = self._column_x(tree, "date")
        x_source = self._column_x(tree, "source")
        if x_date is None or x_source is None:
            # 列表还是空的时候退回按列宽推算
            fixed = sum(int(tree.column(name, "width"))
                        for name in ("id", "date", "source"))
            first = total - fixed
            x_date = first + int(tree.column("id", "width"))
            x_source = x_date + int(tree.column("date", "width"))
        for line, x in zip(dividers, (x_date, x_source)):
            if 0 < x < total - 1:
                line.place(x=x, y=0, width=1, height=height)
                line.lift()
            else:
                line.place_forget()

    # ---------- 模式与视图切换 ----------

    def _remember_concurrency(self, *args):
        if self._concurrency_switching:
            return
        try:
            self.concurrency_by_mode[self.mode] = self.concurrency_var.get()
        except Exception:
            pass
        # 并发数改动实时生效：调大补线程，调小让多出来的线程跑完当前任务再退出
        self._target_workers = self._active_concurrency()
        if getattr(self, "downloading", False):
            self._ensure_workers()

    def _apply_mode_concurrency(self):
        """切模式时换成该模式记住的并发数（整合包默认 16）。"""
        self._concurrency_switching = True
        try:
            self.concurrency_var.set(self.concurrency_by_mode.get(self.mode, "4"))
        finally:
            self._concurrency_switching = False
        self._target_workers = self._active_concurrency()
        if getattr(self, "downloading", False):
            self._ensure_workers()

    def on_mode_change(self):
        self.mode = self.mode_var.get()
        self._apply_mode_concurrency()
        # 整合包页内容多（工具条 + 拖放提示 + 文件清单 + 状态行），
        # 上半区多给一点高度，下半区相应压一点，提示才不会被裁掉。
        if self.mode == "installer":
            self.bottom_half.place_forget()
            self.top_half.place_configure(relheight=1.0)
            # 安装加载器页不需要搜索
            self.search_card.pack_forget()
        elif self.mode == "modpack":
            self.search_card.pack(fill=X, before=self.middle_frame)
            self.bottom_half.place(x=0, rely=0.58, relwidth=1, relheight=0.42)
            self.top_half.place_configure(relheight=0.58)
        else:
            self.search_card.pack(fill=X, before=self.middle_frame)
            self.bottom_half.place(x=0, rely=0.5, relwidth=1, relheight=0.5)
            self.top_half.place_configure(relheight=0.5)
        self.server_tabs_frame.pack_forget()
        self.loader_tabs_frame.pack_forget()
        self.loader_sub_frame.pack_forget()
        self.pri_card.pack_forget()

        if self.mode == "client":
            self.pri_card.pack(fill=X)
            self.switch_view(single=True)
            self.rebuild_client_list()
        elif self.mode == "server":
            self.server_tabs_frame.pack(fill=X, before=self.list_card)
            pid = self.server_provider_var.get()
            if pid == "vanilla":
                self.pri_card.pack(fill=X)
            self.switch_view(single=True)
            if not self.server_data_loaded and not self._server_loading:
                self.load_server_data()
            self.rebuild_server_list()
        elif self.mode == "loader":
            self.loader_tabs_frame.pack(fill=X, before=self.list_card)
            self.pri_card.pack(fill=X)
            self.on_loader_provider_change()
        elif self.mode == "modpack":
            self.switch_view_modpack()
            self.render_modpack()
        elif self.mode == "installer":
            self.switch_view_installer()
            if self.mc_dir_var.get().strip():
                self.scan_installed_versions()
            else:
                self.show_installer_hint()

        is_pack = self.mode in ("modpack", "installer")
        select_state = DISABLED if is_pack else NORMAL
        self.select_all_btn.config(state=select_state)
        self.select_none_btn.config(state=select_state)
        self.download_btn.config(text="开始安装" if self.mode == "modpack" else "开始下载")
        self.update_priority_btn()
        self.update_count()

    def refresh_version_list(self):
        """按当前页面重新拉取版本列表。客户端/原版刷新时保留旧列表，等新数据回来再替换。"""
        if self.mode == "client":
            self.load_client_manifests()
        elif self.mode == "server":
            pid = self.server_provider_var.get()
            if pid == "vanilla":
                self.load_client_manifests()
            else:
                self.server_third.pop(pid, None)
                self.server_data_loaded = False
                self.tree.delete(*self.tree.get_children())
                self.tree.insert(
                    "", "end", text="  正在刷新数据，请稍候…",
                    values=("loading", "", ""), tags=("group",),
                )
                self.load_server_data()
        elif self.mode == "loader":
            if self.loader_mode == "fabric":
                self.fabric_versions = []
                self.fabric_error = None
                self.rebuild_loader_list()
                self.load_fabric_versions()
            elif self.loader_mode == "forge":
                self.forge_loaded = False
                self.forge_index = {}
                self.forge_mc_versions = []
                self.current_forge_mc = None
                self.load_forge_data()
            elif self.loader_mode == "neoforge":
                self.neoforge_loaded = False
                self.neoforge_index = {}
                self.neoforge_mc_versions = []
                self.current_forge_mc = None
                self.load_neoforge_data()
        elif self.mode == "modpack":
            if self.modpack_path and os.path.isfile(self.modpack_path):
                self.load_modpack(self.modpack_path)
            else:
                self.set_pack_status("还没有选择整合包，先拖入或选择 .mrpack / .zip")
        self.update_count()

    def switch_view(self, single=True):
        if hasattr(self, "modpack_frame"):
            self.modpack_frame.pack_forget()
        if hasattr(self, "installer_frame"):
            self.installer_frame.pack_forget()
        if single:
            self.forge_three_col_frame.pack_forget()
            self.single_list_frame.pack(fill=BOTH, expand=True)
        else:
            self.single_list_frame.pack_forget()
            self.forge_three_col_frame.pack(fill=BOTH, expand=True)

    def switch_view_modpack(self):
        self.single_list_frame.pack_forget()
        self.forge_three_col_frame.pack_forget()
        self.installer_frame.pack_forget()
        self.modpack_frame.pack(fill=BOTH, expand=True)

    def switch_view_installer(self):
        self.single_list_frame.pack_forget()
        self.forge_three_col_frame.pack_forget()
        self.modpack_frame.pack_forget()
        self.installer_frame.pack(fill=BOTH, expand=True)

    def on_server_provider_change(self):
        pid = self.server_provider_var.get()
        if pid == "vanilla":
            self.pri_card.pack(fill=X)
        else:
            self.pri_card.pack_forget()

        if pid != "vanilla" and pid not in self.server_third and not self._server_loading:
            self.tree.delete(*self.tree.get_children())
            self.tree.insert(
                "", "end", text="  正在加载数据，请稍候...",
                values=("loading", "", ""), tags=("group",),
            )
            self.load_server_data()
        else:
            self.rebuild_server_list()
        self.update_priority_btn()
        self.update_count()

    def on_loader_provider_change(self):
        self.loader_mode = self.loader_provider_var.get()

        self.loader_sub_frame.pack_forget()
        self.build_loader_sub_ui()
        if self.loader_mode == "fabric":
            self.loader_sub_frame.pack(fill=X, before=self.list_card)
            self.switch_view(single=True)
            if not self.fabric_versions:
                self.load_fabric_versions()
            else:
                self.rebuild_loader_list()
        else:
            self.switch_view(single=False)
            if self.loader_mode == "forge":
                self.load_forge_data()
            elif self.loader_mode == "neoforge":
                self.load_neoforge_data()

        self.update_priority_btn()
        self.update_count()

    def update_priority_btn(self):
        """优先源按钮跟随当前模式：原版是 Mojang 官方，加载器是各自的 Maven 官方。"""
        if self.mode == "client" or (self.mode == "server" and self.server_provider_var.get() == "vanilla"):
            official, mirror = SOURCE_NAMES["mojang"], SOURCE_NAMES["bmclapi"]
        elif self.mode == "loader":
            official, mirror = loader_source_names(self.loader_mode)
        else:
            self.pri_card.pack_forget()
            return

        if mirror:
            name = official if self.priority_source == "mojang" else mirror
            other = mirror if self.priority_source == "mojang" else official
            self.priority_btn.config(
                text=f"优先源：{name}  ->  失败回退 {other}    （点击切换）",
                state=NORMAL,
            )
        else:
            self.priority_btn.config(text=f"下载源：{official}（无镜像回退）", state=DISABLED)

    def build_loader_sub_ui(self):
        for w in self.loader_sub_frame.winfo_children():
            w.destroy()

        if self.loader_mode == "fabric":
            tb.Label(
                self.loader_sub_frame, text="安装器格式：", font=FONT_BODY, bootstyle=SECONDARY
            ).pack(side=LEFT)
            for text, val in [("JAR", "jar"), ("EXE", "exe")]:
                tb.Radiobutton(
                    self.loader_sub_frame, text=text,
                    variable=self.fabric_ext_var, value=val,
                    command=lambda v=val: self.on_fabric_ext_change(v),
                    bootstyle="secondary-toolbutton",
                ).pack(side=LEFT, padx=(4, 0))

    def on_fabric_ext_change(self, val):
        self.fabric_ext = val
        self.fabric_ext_var.set(val)
        self.rebuild_loader_list()

    # ---------- 整合包 ----------

    def set_pack_status(self, text, error=False):
        self.pack_status.configure(
            text=text, bootstyle=(DANGER if error else SECONDARY)
        )

    def choose_modpack(self):
        path = filedialog.askopenfilename(
            title="选择整合包",
            filetypes=[("Minecraft 整合包", "*.mrpack *.zip"), ("所有文件", "*.*")],
        )
        if path:
            self.load_modpack(path)

    def on_files_dropped(self, paths):
        self.mode_var.set("modpack")
        if self.mode != "modpack":
            self.on_mode_change()
        files = [p for p in paths if p]
        if not files:
            return
        if len(files) > 1:
            self.set_pack_status("一次只能处理一个整合包，已使用拖入的第一个文件", error=True)
        path = files[0]
        if os.path.isdir(path):
            self.set_pack_status("拖入的是文件夹，请拖 .mrpack / .zip 文件", error=True)
            return
        self.load_modpack(path)

    def load_modpack(self, path):
        if self.downloading:
            self.set_pack_status("正在下载/安装中，请先停止再更换整合包", error=True)
            return
        if self.modpack_inspecting:
            self.set_pack_status("上一个整合包还在检查，请稍候", error=True)
            return
        self.modpack_inspecting = True
        self.modpack_path = path
        self.modpack = None
        self.modpack_files = {}
        self.pack_tree.delete(*self.pack_tree.get_children())
        self.pack_info_label.configure(text=f"正在检查：{os.path.basename(path)}")
        self.set_pack_status("正在检查文件格式…")

        def worker():
            try:
                pack = inspect_modpack(path)
            except ModpackError as e:
                self.queue.put(("modpack_error", str(e)))
            except Exception as e:
                self.queue.put(("modpack_error", f"读取失败：{e}"))
            else:
                self.queue.put(("modpack_ready", pack))

        threading.Thread(target=worker, daemon=True).start()

    def on_modpack_ready(self, pack):
        self.modpack_inspecting = False
        self.modpack = pack
        self.modpack_files = {f["path"]: f for f in pack["files"]}
        self.modpack_failures = 0
        self.modpack_finished = False
        self._version_json_path = None
        self._version_json_error = None
        self.render_modpack()
        self.pack_info_label.configure(
            text=f"{pack['name']}  |  MC {pack['mc'] or '未声明'}  |  {modpack_loader_text(pack)}"
        )
        self.set_pack_status(
            f"已识别为整合包（{pack['kind']}）：{len(pack['files'])} 个待下载文件，"
            f"{len(pack['local_files'])} 个内置文件"
        )
        self.update_count()

    def on_modpack_error(self, message):
        self.modpack_inspecting = False
        self.modpack = None
        self.modpack_files = {}
        self.pack_tree.delete(*self.pack_tree.get_children())
        self.pack_info_label.configure(text="未选择整合包")
        self.set_pack_status(f"不是可用的整合包：{message}", error=True)
        self.update_count()

    def clear_modpack(self):
        self.modpack = None
        self.modpack_path = ""
        self.modpack_files = {}
        self.modpack_failures = 0
        self.modpack_finished = False
        self._version_json_path = None
        self._version_json_error = None
        self.pack_tree.delete(*self.pack_tree.get_children())
        self.pack_info_label.configure(text="未选择整合包")
        self.set_pack_status("支持 Modrinth .mrpack，以及带 mods/、config/ 的 .zip")
        self.update_count()

    def render_modpack(self):
        self.pack_tree.delete(*self.pack_tree.get_children())
        if not self.modpack:
            return
        search = self.search_var.get().strip().lower()
        entries = []
        for item in self.modpack["files"]:
            entries.append((item["path"], "下载", item.get("size") or 0))
        for item in self.modpack["local_files"]:
            entries.append((item["path"], "内置", None))
        if search:
            entries = [e for e in entries if search in e[0].lower()]

        groups = {}
        for path, source, size in entries:
            head = path.split("/", 1)[0] if "/" in path else "（根目录）"
            groups.setdefault(head, []).append((path, source, size))

        order = ["mods", "config", "defaultconfigs", "resourcepacks",
                 "shaderpacks", "datapacks", "kubejs", "scripts"]
        keys = sorted(
            groups,
            key=lambda k: (order.index(k) if k in order else len(order), k.lower()),
        )
        for head in keys:
            items = groups[head]
            parent = self.pack_tree.insert(
                "", "end", text=f"  {head} ({len(items)})",
                values=("", PACK_DIR_TYPES.get(head, "目录"), ""),
                tags=("group",), open=True,
            )
            for path, source, size in sorted(items):
                self.pack_tree.insert(
                    parent, "end", text=f"  {path}",
                    values=(
                        human(size) if size else "",
                        _pack_type_for(path),
                        "内置文件" if source == "内置" else "待下载",
                    ),
                    tags=("item",),
                )
        self.update_count()

    def modpack_target_dir(self):
        base = self.out_dir_var.get().strip()
        if not base or not self.modpack:
            return ""
        return os.path.join(base, sanitize_folder_name(self.modpack["name"]))

    def start_modpack_install(self, max_workers):
        pack = self.modpack
        target = self.modpack_target_dir()
        if not target:
            self.set_pack_status("请先选择可用的下载目录", error=True)
            return
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            self.set_pack_status(f"无法创建整合包文件夹：{e}", error=True)
            return

        self.modpack_failures = 0
        self.modpack_finished = False
        self._modpack_installing = True
        self._modpack_target = target
        self._version_json_path = None
        self._version_json_error = None
        # 版本 JSON 可能要拉 Fabric profile 或 7~9MB 的 Forge/NeoForge 安装器，
        # 放后台线程做，避免卡住界面；写文件在主线程的消息处理里完成。
        threading.Thread(
            target=self._prepare_version_json, args=(pack,), daemon=True
        ).start()
        tasks = [("modpack", f["path"], f) for f in sorted(pack["files"], key=lambda x: x["path"])]

        # 原版 jar 以整合包名命名，和版本 JSON、启动器隔离目录保持一致
        extras = []
        mc = str(pack.get("mc") or "")
        folder_name = os.path.basename(os.path.normpath(target))
        if mc:
            extras.append(("modpack_extra", "原版 JAR", {
                "type": "vanilla_jar", "mc": mc,
                "filename": f"{folder_name}.jar",
            }))
        self._modpack_extra_vids = {vid for _, vid, _ in extras}
        self._modpack_extra_failures = 0
        tasks.extend(extras)

        self.set_pack_status(f"开始安装到：{target}")
        self._modpack_remaining = len(tasks)
        self._modpack_vids = {vid for _, vid, _ in tasks}
        if tasks:
            if not self.submit_tasks(tasks):
                self._modpack_installing = False
        else:
            self.finish_modpack()

    def _prepare_version_json(self, pack):
        try:
            data = build_modpack_version_json(pack)
            self.queue.put(("modpack_version_json", data, None))
        except Exception as e:
            self.queue.put(("modpack_version_json", None, str(e)))

    def prepare_modpack_file(self, rel_path):
        info = self.modpack_files.get(rel_path)
        if not info:
            raise RuntimeError(f"整合包清单里找不到 {rel_path}")
        base = self._download_out_dir
        target_dir = os.path.join(base, sanitize_folder_name(self.modpack["name"]))
        target = os.path.join(target_dir, *rel_path.split("/"))
        candidates = []
        for url in info.get("urls") or []:
            host = urllib.parse.urlsplit(url).netloc or "下载源"
            if host.endswith("modrinth.com"):
                host = "Modrinth CDN"
            candidates.append(self._candidate(
                host, url, info.get("size") or 0, info.get("sha1"), None,
                os.path.basename(rel_path),
            ))
        if not candidates:
            raise RuntimeError(f"{rel_path} 没有可用下载地址")
        return {"vid": rel_path, "path": target, "candidates": candidates}

    def prepare_modpack_extra(self, vid, info):
        """整合包附带的原版 jar（按整合包名命名）；已存在就跳过。"""
        target_dir = getattr(self, "_modpack_target", "") or self._download_out_dir
        filename = info.get("filename") or f"{vid}.jar"
        path = os.path.join(target_dir, filename)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return {"vid": vid, "skip": True}

        candidates = []
        if info.get("type") == "vanilla_jar":
            data = _vanilla_version_json(str(info.get("mc") or ""))
            client = (data.get("downloads") or {}).get("client") or {}
            url = client.get("url")
            if not url:
                raise RuntimeError("原版版本 JSON 里没有 client 下载地址")
            candidates.append(self._candidate(
                "Mojang 官方", url, client.get("size") or 0, client.get("sha1"), None,
                filename,
            ))
        else:
            for url in info.get("urls") or []:
                host = urllib.parse.urlsplit(url).netloc or "下载源"
                candidates.append(self._candidate(host, url, 0, None, None, filename))
        if not candidates:
            raise RuntimeError(f"{filename} 没有可用下载地址")
        return {"vid": vid, "path": path, "candidates": candidates}

    def extract_modpack_overrides(self, pack, target):
        local_files = pack.get("local_files") or []
        if not local_files:
            return 0
        written = 0
        with zipfile.ZipFile(pack["path"]) as zf:
            for item in local_files:
                dest = os.path.join(target, *item["path"].split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(item["zip_name"]) as src, open(dest, "wb") as dst:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        dst.write(chunk)
                written += 1
        return written

    def write_modpack_info(self, pack, target):
        version_json = getattr(self, "_version_json_path", None)
        version_json_error = getattr(self, "_version_json_error", None)
        if version_json:
            json_line = f"{os.path.basename(version_json)}（PCL / HMCL 版本隔离可直接识别）"
        elif version_json_error:
            json_line = f"生成失败：{version_json_error}"
        else:
            json_line = "生成中…"

        folder_name = os.path.basename(os.path.normpath(target))
        groups = {}
        for item in pack.get("files") or []:
            head = item["path"].split("/", 1)[0] if "/" in item["path"] else "根目录"
            groups[head] = groups.get(head, 0) + 1
        group_line = "、".join(f"{k} {v} 个" for k, v in sorted(groups.items())) or "无"
        lines = [
            f"整合包：{pack['name']}",
            f"整合包版本：{pack['version'] or '未声明'}",
            f"整合包格式：{pack.get('kind', '')}",
            f"Minecraft 版本：{pack['mc'] or '未声明'}",
            f"Mod 加载器：{modpack_loader_text(pack)}",
            "加载器安装器：未下载（需要时请用 PCL / HMCL 安装对应加载器，或用整合包内自带文件）",
            f"版本 JSON：{json_line}",
            f"原版 JAR：{folder_name}.jar",
            f"远程文件：{len(pack.get('files') or [])} 个（{group_line}）",
            f"内置文件：{len(pack.get('local_files') or [])} 个（overrides / client-overrides）",
            f"下载失败：{self.modpack_failures} 个整合包文件"
            + (f"，{self._modpack_extra_failures} 个附加文件" if self._modpack_extra_failures else ""),
            f"来源文件：{pack['path']}",
            f"安装目录：{target}",
            f"安装时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        path = os.path.join(target, "整合包信息.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def on_modpack_version_json(self, data, err):
        if err or not data:
            self._version_json_error = err or "未知错误"
        else:
            target = getattr(self, "_modpack_target", "")
            if not target:
                self._version_json_error = "整合包目录尚未创建"
            else:
                try:
                    os.makedirs(target, exist_ok=True)
                    folder = os.path.basename(os.path.normpath(target))
                    self._version_json_path = write_modpack_version_json(
                        target, folder, data
                    )
                except Exception as e:
                    self._version_json_error = str(e)
                    log_exception("version_json")
        # 如果安装信息已经写过，补写一次让“版本 JSON”那一行反映最终结果
        if self.modpack_finished and self.modpack and getattr(self, "_modpack_target", ""):
            try:
                self.write_modpack_info(self.modpack, self._modpack_target)
            except Exception:
                pass
            if self._version_json_path:
                self.set_pack_status(
                    f"安装完成，版本 JSON 已写入：{os.path.basename(self._version_json_path)}"
                )
            elif self._version_json_error:
                self.set_pack_status(
                    f"安装完成，但版本 JSON 生成失败：{self._version_json_error}",
                    error=True,
                )

    def finish_modpack(self):
        if not self.modpack or self.modpack_finished:
            return
        pack = self.modpack
        target = getattr(self, "_modpack_target", "") or self.modpack_target_dir()
        self.set_pack_status("正在写入内置配置文件并生成整合包信息…")
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            written = self.extract_modpack_overrides(pack, target)
            self.write_modpack_info(pack, target)
        except Exception as e:
            self._modpack_installing = False
            log_exception("finish_modpack")
            self.set_pack_status(f"写入内置文件失败：{e}", error=True)
            return
        self.modpack_finished = True
        self._modpack_installing = False
        self.refresh_filetree()
        failed = self.modpack_failures
        extra_failed = getattr(self, "_modpack_extra_failures", 0)
        if failed or extra_failed:
            parts = []
            if failed:
                parts.append(f"{failed} 个整合包文件下载失败")
            if extra_failed:
                parts.append(f"{extra_failed} 个原版 jar 下载失败")
            self.set_pack_status(
                "安装结束：" + "，".join(parts) + f"，内置文件写入 {written} 个", error=True
            )
        else:
            pending = ""
            if not self._version_json_path and not self._version_json_error:
                pending = "；版本 JSON 正在生成"
            self.set_pack_status(
                f"安装完成：已写入 {written} 个内置文件，目录 {target}{pending}"
            )
        self.update_count()

    # ---------- 安装加载器 ----------

    def show_installer_hint(self):
        self.installer_hint.configure(
            text="请选择或输入 .minecraft 文件夹（其中应包含 versions 子目录）"
        )
        self.installed_tree.delete(*self.installed_tree.get_children())
        self._reset_installer_options()

    def choose_mc_dir(self):
        path = filedialog.askdirectory(
            initialdir=self.mc_dir_var.get().strip() or os.getcwd(),
            title="选择 .minecraft 文件夹",
        )
        if path:
            self.mc_dir_var.set(path)
            self.scan_installed_versions()

    def _versions_dir(self):
        base = self.mc_dir_var.get().strip()
        if not base:
            return ""
        return os.path.join(base, "versions")

    def scan_installed_versions(self):
        self.installed_tree.delete(*self.installed_tree.get_children())
        self.installed_versions = []
        self._reset_installer_options()
        versions_dir = self._versions_dir()
        if not versions_dir or not os.path.isdir(versions_dir):
            self.installer_hint.configure(
                text="路径无效：请选择包含 versions 子目录的 .minecraft 文件夹"
            )
            return
        for name in sorted(os.listdir(versions_dir), key=str.lower):
            folder = os.path.join(versions_dir, name)
            if not os.path.isdir(folder):
                continue
            jar = os.path.join(folder, name + ".jar")
            js = os.path.join(folder, name + ".json")
            if os.path.isfile(jar) and os.path.isfile(js):
                self.installed_versions.append(name)
                self.installed_tree.insert("", "end", text=name, values=(name,))
        if self.installed_versions:
            self.installer_hint.configure(
                text=f"共找到 {len(self.installed_versions)} 个包含版本 JAR + JSON 的版本"
            )
        else:
            self.installer_hint.configure(
                text="versions 里没有同时包含 <名称>.jar 和 <名称>.json 的版本文件夹"
            )

    def _manifest_version_from_text(self, text):
        """从任意字符串里找已安装清单中的版本号，例如 1.21.1-Forge_47.2.0。"""
        if not isinstance(text, str) or not text:
            return ""
        for vid in sorted(self.versions, key=len, reverse=True):
            start = 0
            while True:
                index = text.find(vid, start)
                if index < 0:
                    break
                before = text[index - 1] if index > 0 else ""
                after = text[index + len(vid)] if index + len(vid) < len(text) else ""
                if (not before or not (before.isdigit() or before == ".")) and (
                    not after or not (after.isdigit() or after == ".")
                ):
                    return vid
                start = index + 1
        return ""

    def _mc_version_from_json(self, name, data, versions_dir, depth=0):
        candidates = []
        for key in ("clientVersion", "jar", "id", "inheritsFrom"):
            value = data.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
        for value in candidates:
            if value in self.versions:
                return value
        parent = data.get("inheritsFrom")
        if isinstance(parent, str) and depth < 3:
            parent_json = os.path.join(versions_dir, parent, parent + ".json")
            if os.path.isfile(parent_json):
                try:
                    with open(parent_json, encoding="utf-8") as f:
                        parent_data = json.load(f)
                    found = self._mc_version_from_json(parent, parent_data, versions_dir, depth + 1)
                    if found:
                        return found
                except Exception:
                    pass
            else:
                found = self._manifest_version_from_text(parent)
                if found:
                    return found
        for value in candidates:
            found = self._manifest_version_from_text(value)
            if found:
                return found
        for value in candidates:
            if re.match(r"^\d+\.\d+(\.\d+)?$", value):
                return value
        return ""

    @staticmethod
    def _detect_installed_loader(data):
        main = str(data.get("mainClass") or "").lower()
        libs = " ".join(
            str(lib.get("name") or "") for lib in (data.get("libraries") or [])
        ).lower()
        if "fabricmc" in main or "fabric-loader" in libs:
            return "Fabric"
        if "quiltmc" in main or "quilt-loader" in libs:
            return "Quilt"
        if "neoforge" in main or "neoforge" in libs:
            return "NeoForge"
        if "forge" in main or "minecraftforge" in libs:
            return "Forge"
        return ""

    def _reset_installer_options(self):
        self.installed_selected = None
        self.installed_mc = ""
        self._installed_loader = ""
        self._loader_req += 1
        self.installer_loader_var.set("")
        self.installer_loader_version_var.set("")
        self.installer_loader_combo.configure(state=DISABLED, values=("Fabric", "Forge", "NeoForge"))
        self.installer_version_combo.configure(state=DISABLED, values=())
        self.installer_btn.configure(state=DISABLED)
        self.installed_path_label.configure(text="未选择游戏版本")
        self.game_version_label.configure(text="游戏版本：-")
        self.installer_status.configure(text="")

    def on_installed_version_select(self, event=None):
        selection = self.installed_tree.selection()
        if not selection:
            self._reset_installer_options()
            return
        name = self.installed_tree.item(selection[0], "text")
        versions_dir = self._versions_dir()
        folder = os.path.join(versions_dir, name)
        json_path = os.path.join(folder, name + ".json")
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._reset_installer_options()
            self.installer_status.configure(text=f"读取版本 JSON 失败：{e}", bootstyle=DANGER)
            return
        mc = self._mc_version_from_json(name, data, versions_dir)
        installed_loader = self._detect_installed_loader(data)
        self.installed_selected = name
        self.installed_mc = mc
        self._installed_loader = installed_loader
        self.installed_path_label.configure(text=f"版本路径：{folder}")
        self.game_version_label.configure(text=f"游戏版本：{mc or '未知'}")
        # 切换游戏版本：清空加载器 / 加载器版本选择
        self._loader_req += 1
        self.installer_loader_var.set("")
        self.installer_loader_version_var.set("")
        self.installer_version_combo.configure(state=DISABLED, values=())
        self.installer_btn.configure(state=DISABLED)
        if installed_loader:
            self.installer_loader_combo.configure(state=DISABLED)
            self.installer_status.configure(
                text=f"该版本已安装 {installed_loader} 加载器，禁止重复安装",
                bootstyle=DANGER,
            )
        elif mc:
            self.installer_loader_combo.configure(state="readonly")
            self.installer_status.configure(
                text="请选择要安装的加载器", bootstyle=SECONDARY
            )
        else:
            self.installer_loader_combo.configure(state=DISABLED)
            self.installer_status.configure(
                text="无法从版本 JSON 判断游戏版本，不能安装加载器", bootstyle=DANGER
            )

    def on_installer_loader_change(self):
        if self._installed_loader:
            return
        self.installer_loader_version_var.set("")
        self.installer_version_combo.configure(state=DISABLED, values=())
        self.installer_btn.configure(state=DISABLED)
        loader = self.installer_loader_var.get()
        if not loader or not self.installed_mc:
            return
        self._loader_req += 1
        request_id = self._loader_req
        self.installer_status.configure(
            text=f"正在获取 {loader} 适用于 {self.installed_mc} 的版本…", bootstyle=SECONDARY
        )
        threading.Thread(
            target=self._fetch_loader_versions_worker,
            args=(request_id, self.installed_mc, loader),
            daemon=True,
        ).start()

    def _fetch_loader_versions_worker(self, request_id, mc, loader):
        try:
            versions = fetch_loader_versions(mc, loader)
            self.queue.put(("loader_versions", request_id, versions, None))
        except Exception as e:
            self.queue.put(("loader_versions", request_id, [], str(e)))

    def on_loader_versions(self, request_id, versions, error):
        if request_id != self._loader_req:
            return
        if error:
            self.installer_version_combo.configure(state=DISABLED, values=())
            self.installer_status.configure(text=f"获取加载器版本失败：{error}", bootstyle=DANGER)
            return
        if not versions:
            self.installer_version_combo.configure(state=DISABLED, values=())
            self.installer_status.configure(text="没有可用版本", bootstyle=DANGER)
            return
        self.installer_version_combo.configure(
            state="readonly", values=tuple(versions)
        )
        self.installer_status.configure(
            text=f"共 {len(versions)} 个加载器版本，请选择", bootstyle=SECONDARY
        )

    def on_installer_version_change(self):
        ready = bool(
            self.installed_selected
            and self.installer_loader_var.get()
            and self.installer_loader_version_var.get()
        )
        self.installer_btn.configure(state=NORMAL if ready else DISABLED)

    def install_selected_loader(self):
        if self._installer_busy or self._installed_loader:
            return
        name = self.installed_selected
        loader = self.installer_loader_var.get()
        loader_version = self.installer_loader_version_var.get()
        mc = self.installed_mc
        if not (name and loader and loader_version and mc):
            return
        folder = os.path.join(self._versions_dir(), name)
        self._installer_busy = True
        self.installer_btn.configure(state=DISABLED)
        self.installer_status.configure(text="正在安装加载器（不影响其他下载）…", bootstyle=SECONDARY)
        threading.Thread(
            target=self._install_loader_worker,
            args=(name, folder, mc, loader, loader_version),
            daemon=True,
        ).start()

    def _install_loader_worker(self, name, folder, mc, loader, loader_version):
        json_path = os.path.join(folder, name + ".json")
        try:
            overlay = fetch_loader_overlay(mc, loader.lower(), loader_version)
            with open(json_path, encoding="utf-8") as f:
                base = json.load(f)
            merged = _merge_version_json(base, overlay)
            merged["id"] = name
            backup = json_path + ".bak"
            if not os.path.exists(backup):
                shutil.copy2(json_path, backup)
            tmp = json_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            os.replace(tmp, json_path)
            self.queue.put(("loader_install_done", name, loader, loader_version, None))
        except Exception as e:
            self.queue.put(("loader_install_done", name, loader, loader_version, str(e)))

    def on_loader_install_done(self, name, loader, loader_version, error):
        self._installer_busy = False
        try:
            self.installer_btn.configure(
                state=NORMAL if self.installer_loader_version_var.get() else DISABLED
            )
        except Exception:
            pass
        if error:
            self.installer_status.configure(text=f"安装失败：{error}", bootstyle=DANGER)
            messagebox.showerror("安装失败", f"向版本 {name} 安装 {loader} 失败：\n{error}")
        else:
            self.installer_status.configure(
                text=f"已把 {loader} {loader_version} 注入版本 {name}", bootstyle=SUCCESS
            )
            messagebox.showinfo(
                "安装完成", f"已把 {loader} {loader_version} 注入版本：\n{name}"
            )

    # ---------- 客户端清单加载 ----------

    def load_client_manifests(self):
        if self._manifests_loading:
            return
        self._manifests_loading = True

        def worker():
            with cf.ThreadPoolExecutor(max_workers=2) as pool:
                futs = [
                    pool.submit(self._fetch_manifest, "mojang", MOJANG_MANIFEST),
                    pool.submit(self._fetch_manifest, "bmclapi", BMCLAPI_MANIFEST),
                ]
                results = [f.result() for f in futs]
            self.queue.put(("client_manifests", results))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _fetch_manifest(source, url):
        t0 = time.time()
        try:
            data = json.loads(http_get(url).decode("utf-8"))
            return source, data, time.time() - t0, None
        except Exception as e:
            return source, None, time.time() - t0, e

    def on_client_manifests(self, results):
        self._manifests_loading = False
        for source, data, _, err in results:
            if not data:
                continue
            for item in data.get("versions", []):
                vid = item.get("id")
                if not vid:
                    continue
                entry = {
                    "id": vid,
                    "type": item.get("type", "unknown"),
                    "releaseTime": item.get("releaseTime", ""),
                    "url": item.get("url"),
                }
                if vid not in self.versions:
                    self.versions[vid] = {
                        "id": vid,
                        "type": entry["type"],
                        "releaseTime": entry["releaseTime"],
                        "sources": {},
                    }
                self.versions[vid]["sources"][source] = entry["url"]

        if self.mode == "client":
            self.rebuild_client_list()
        elif self.mode == "server":
            self.rebuild_server_list()
        self.download_btn.config(state=NORMAL)

    # ---------- 单列表构建 ----------

    def rebuild_client_list(self):
        tree = self.tree
        tree.delete(*tree.get_children())
        groups = {}
        for vid, info in self.versions.items():
            t = "april" if vid in APRIL_FOOLS else info["type"]
            groups.setdefault(t, []).append((vid, info))

        search = self.search_var.get().strip().lower()
        for t in TYPE_ORDER:
            if t not in groups: continue
            items = groups[t]
            if search:
                items = [i for i in items if search in i[0].lower()]
                if not items: continue
            items.sort(key=lambda x: x[1]["releaseTime"], reverse=True)
            parent = tree.insert("", "end", text=f"  {TYPE_NAMES[t]} ({len(items)})", values=(t, "", ""), tags=("group",), open=t not in self.client_collapsed)
            for vid, info in items:
                mark = "☑" if vid in self.client_checked else "☐"
                src_str = source_label(info, self.priority_source)
                tree.insert(parent, "end", text=f"  {mark} {vid}", values=(vid, info["releaseTime"][:10], src_str), tags=("item",))
        self.update_count()

    def rebuild_server_list(self):
        tree = self.tree
        tree.delete(*tree.get_children())
        pid = self.server_provider_var.get()
        search = self.search_var.get().strip().lower()

        if pid == "vanilla":
            groups = {}
            for vid, info in self.versions.items():
                t = "april" if vid in APRIL_FOOLS else info["type"]
                groups.setdefault(t, []).append((vid, info))
            checked = self.server_checked.setdefault("vanilla", set())
            for t in TYPE_ORDER:
                if t not in groups: continue
                items = groups[t]
                if search:
                    items = [i for i in items if search in i[0].lower()]
                    if not items: continue
                items.sort(key=lambda x: x[1]["releaseTime"], reverse=True)
                parent = tree.insert("", "end", text=f"  {TYPE_NAMES[t]} ({len(items)})", values=(t, "", ""), tags=("group",), open=True)
                for vid, info in items:
                    mark = "☑" if vid in checked else "☐"
                    tree.insert(parent, "end", text=f"  {mark} {vid}", values=(vid, info["releaseTime"][:10], source_label(info, self.priority_source)), tags=("item",))
        else:
            data = self.server_third.get(pid, {})
            if not data:
                tree.insert("", "end", text="  正在加载数据，请稍候...", values=("loading", "", ""), tags=("group",))
            elif "__error__" in data:
                tree.insert("", "end", text=f"  加载失败：{data['__error__'][:80]}", values=("error", "", ""), tags=("group",))
            else:
                checked = self.server_checked.setdefault(pid, set())
                items = list(data.items())
                if search: items = [i for i in items if search in i[0].lower()]
                items.sort(key=lambda x: version_key(x[0]), reverse=True)
                parent = tree.insert("", "end", text=f"  {PROVIDER_NAMES[pid]} ({len(items)})", values=(pid, "", ""), tags=("group",), open=True)
                for vid, info in items[:300]:
                    mark = "☑" if vid in checked else "☐"
                    tree.insert(parent, "end", text=f"  {mark} {vid}", values=(vid, "", PROVIDER_NAMES[pid]), tags=("item",))
        self.update_count()

    def load_fabric_versions(self):
        if self._fabric_loading:
            return
        self._fabric_loading = True
        self.fabric_versions = []

        def worker():
            try:
                vers = fetch_fabric_installer_versions()
                self.queue.put(("fabric_versions", vers))
            except Exception as e:
                self.queue.put(("fabric_versions", [], str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def on_fabric_versions(self, vers, err=None):
        self._fabric_loading = False
        if err:
            self.fabric_error = str(err)
            self.rebuild_loader_list()
            messagebox.showerror("加载失败", f"Fabric 安装器版本获取失败：{err}")
            return
        self.fabric_error = None
        self.fabric_versions = list(vers)
        self.rebuild_loader_list()

    def rebuild_loader_list(self):
        tree = self.tree
        tree.delete(*tree.get_children())
        search = self.search_var.get().strip().lower()

        if self.loader_mode == "fabric":
            checked = self.fabric_checked
            items = sorted(self.fabric_versions, key=version_key, reverse=True)
            if search:
                items = [i for i in items if search in i.lower()]
            if not items and not self.fabric_versions:
                if self.fabric_error:
                    text = f"  加载失败：{self.fabric_error[:80]}"
                else:
                    text = "  正在加载 Fabric 安装器版本…"
                tree.insert("", "end", text=text, values=("loading", "", ""), tags=("group",))
                return
            parent = tree.insert(
                "", "end", text=f"  Fabric 安装器 ({len(items)})",
                values=("fabric", "", ""), tags=("group",), open=True,
            )
            source = LOADER_OFFICIAL_NAMES["fabric"]
            for v in items[:200]:
                mark = "☑" if v in checked else "☐"
                tree.insert(
                    parent, "end", text=f"  {mark} {v}",
                    values=(v, "", source), tags=("item",),
                )
        self.update_count()

    # ---------- 三列构建 ----------

    def _set_three_col_placeholder(self, text):
        self.col_mc_tree.delete(*self.col_mc_tree.get_children())
        self.col_main_tree.delete(*self.col_main_tree.get_children())
        self.col_all_tree.delete(*self.col_all_tree.get_children())
        self.col_mc_tree.insert("", "end", text=text, values=("",), tags=("placeholder",))

    def load_forge_data(self):
        if self._forge_loading:
            return
        if self.forge_loaded:
            self.rebuild_forge_views()
            return
        self._forge_loading = True
        self._set_three_col_placeholder("正在加载 Forge 版本…")

        def worker():
            data, err = None, None
            try:
                data = (fetch_forge_all_versions(), fetch_forge_promos())
            except Exception as e:
                err = str(e)
            self.queue.put(("forge_data", data, err))
        threading.Thread(target=worker, daemon=True).start()

    def on_forge_data(self, data, err):
        self._forge_loading = False
        if err or not data:
            self._set_three_col_placeholder(f"加载失败：{err}")
            messagebox.showerror("加载失败", f"Forge 数据获取失败：{err}")
            return
        versions, promos = data
        self.forge_promos = promos
        self.forge_index, self.forge_mc_versions = build_forge_index(
            versions, promos, set(self.versions)
        )
        self.forge_loaded = True
        if self.mode == "loader" and self.loader_mode == "forge":
            self.rebuild_forge_views()

    def load_neoforge_data(self):
        if self._neoforge_loading:
            return
        if self.neoforge_loaded:
            self.rebuild_forge_views()
            return
        self._neoforge_loading = True
        self._set_three_col_placeholder("正在加载 NeoForge 版本…")

        def worker():
            versions, err = [], None
            try:
                versions = fetch_neoforge_all_versions()
            except Exception as e:
                err = str(e)
            self.queue.put(("neoforge_data", versions, err))
        threading.Thread(target=worker, daemon=True).start()

    def on_neoforge_data(self, versions, err):
        self._neoforge_loading = False
        if err:
            self._set_three_col_placeholder(f"加载失败：{err}")
            messagebox.showerror("加载失败", f"NeoForge 数据获取失败：{err}")
            return
        self.neoforge_index, self.neoforge_mc_versions = build_neoforge_index(
            versions, set(self.versions)
        )
        self.neoforge_loaded = True
        if self.mode == "loader" and self.loader_mode == "neoforge":
            self.rebuild_forge_views()

    def rebuild_forge_views(self):
        self.col_mc_tree.delete(*self.col_mc_tree.get_children())
        self.col_main_tree.delete(*self.col_main_tree.get_children())
        self.col_all_tree.delete(*self.col_all_tree.get_children())

        if self.loader_mode == "forge":
            mc_vers = list(self.forge_mc_versions)
        else:
            mc_vers = list(self.neoforge_mc_versions)

        search = self.search_var.get().strip().lower()
        if search:
            mc_vers = [m for m in mc_vers if search in m.lower()]

        if not mc_vers:
            self.current_forge_mc = None
            self._set_three_col_placeholder("暂无版本数据")
            return

        for mc in mc_vers:
            self.col_mc_tree.insert("", "end", text=mc, values=(mc,))

        if not self.current_forge_mc or self.current_forge_mc not in mc_vers:
            self.current_forge_mc = mc_vers[0]

        for child in self.col_mc_tree.get_children():
            if self.col_mc_tree.item(child, "text") == self.current_forge_mc:
                self.col_mc_tree.selection_set(child)
                self.col_mc_tree.see(child)
                break
        self.update_forge_columns(self.current_forge_mc)

    def on_mc_select(self, event):
        sel = self.col_mc_tree.selection()
        if sel:
            mc = self.col_mc_tree.item(sel[0], "text")
            if mc != self.current_forge_mc and mc in self._current_mc_versions():
                self.current_forge_mc = mc
                self.update_forge_columns(mc)

    def _current_mc_versions(self):
        if self.loader_mode == "forge":
            return self.forge_mc_versions
        return self.neoforge_mc_versions

    def _insert_forge_items(self, tree, entries, checked):
        for label, vid in entries:
            if not vid:
                continue
            mark = "☑" if vid in checked else "☐"
            if label:
                text = f"  {mark} {label}: {vid}"
            else:
                text = f"  {mark} {vid}"
            tree.insert("", "end", text=text, values=(vid,), tags=("item",))

    def update_forge_columns(self, mc):
        self.col_main_tree.delete(*self.col_main_tree.get_children())
        self.col_all_tree.delete(*self.col_all_tree.get_children())
        if not mc:
            return

        if self.loader_mode == "forge":
            checked = self.forge_checked
            builds = self.forge_index.get(mc, [])
            promos = self.forge_promos.get(mc, {})
            latest = promos.get("latest")
            recommended = promos.get("recommended")
            text_latest = f"{mc}-{latest}" if latest else latest_stable(builds)
            text_recommended = f"{mc}-{recommended}" if recommended else None
            entries = []
            if text_latest:
                entries.append(("最新版", text_latest))
            if text_recommended and text_recommended != text_latest:
                entries.append(("稳定版", text_recommended))
            self._insert_forge_items(self.col_main_tree, entries, checked)
            self._insert_forge_items(self.col_all_tree, [(None, v) for v in builds], checked)

        elif self.loader_mode == "neoforge":
            checked = self.neoforge_checked
            builds = self.neoforge_index.get(mc, [])
            latest = latest_stable(builds)
            entries = [("最新版", latest)] if latest else []
            self._insert_forge_items(self.col_main_tree, entries, checked)
            self._insert_forge_items(self.col_all_tree, [(None, v) for v in builds], checked)

    def on_three_col_press(self, event, tree):
        item = tree.identify_row(event.y)
        if not item: return
        tags = tree.item(item, "tags")
        if "item" not in tags: return
        values = tree.item(item, "values")
        if not values: return
        vid = values[0]
        checked = self.get_current_checked()
        if vid in checked:
            checked.remove(vid)
        else:
            checked.add(vid)
        self.refresh_forge_marks()
        self.update_count()
        return "break"

    def refresh_forge_marks(self):
        checked = self.get_current_checked()
        for tree in (self.col_main_tree, self.col_all_tree):
            for item in tree.get_children():
                if "item" not in tree.item(item, "tags"): continue
                values = tree.item(item, "values")
                if not values: continue
                vid = values[0]
                mark = "☑" if vid in checked else "☐"
                text = tree.item(item, "text")
                for old in ("☐", "☑"):
                    if old in text:
                        text = text.replace(old, mark, 1)
                        break
                tree.item(item, text=text)

    # ---------- 交互通用 ----------

    def on_tree_press(self, event):
        item = self.tree.identify_row(event.y)
        if not item: return
        tags = self.tree.item(item, "tags")
        values = self.tree.item(item, "values")
        if not values: return

        element = ""
        try:
            element = self.tree.identify_element(event.x, event.y) or ""
        except Exception:
            element = ""
        is_indicator = "indicator" in element.lower()

        if "group" in tags:
            if is_indicator:
                self.tree.item(item, open=not bool(self.tree.item(item, "open")))
                return "break"
            self.toggle_group_selection(item)
            return "break"

        if "item" in tags:
            vid = values[0]
            if vid in ("error", "loading", ""): return "break"
            checked = self.get_current_checked()
            if vid in checked:
                checked.discard(vid)
            else:
                checked.add(vid)
            self.refresh_marks()
            self.update_count()
            return "break"

    def toggle_group_selection(self, group_item):
        children = self.tree.get_children(group_item)
        vids = []
        for item in children:
            if "item" not in self.tree.item(item, "tags"): continue
            values = self.tree.item(item, "values")
            if values and values[0] not in ("error", "loading", ""):
                vids.append(values[0])
        if not vids: return
        checked = self.get_current_checked()
        all_selected = all(v in checked for v in vids)
        if all_selected:
            for v in vids: checked.discard(v)
        else:
            checked.update(vids)
        self.refresh_marks()
        self.update_count()

    def get_current_checked(self):
        if self.mode == "client":
            return self.client_checked
        elif self.mode == "server":
            return self.server_checked.setdefault(self.server_provider_var.get(), set())
        elif self.mode == "modpack":
            return set()
        else:
            if self.loader_mode == "fabric":
                return self.fabric_checked
            elif self.loader_mode == "forge":
                return self.forge_checked
            else:
                return self.neoforge_checked

    def _apply_mark(self, item, mark):
        text = self.tree.item(item, "text")
        replaced = False
        for old in ("☐", "☑"):
            if old in text:
                text = text.replace(old, mark, 1)
                replaced = True
                break
        if not replaced: text = f"  {mark} {text.strip()}"
        self.tree.item(item, text=text)

    def refresh_marks(self):
        checked = self.get_current_checked()
        for parent in self.tree.get_children():
            for item in self.tree.get_children(parent):
                if "item" not in self.tree.item(item, "tags"): continue
                values = self.tree.item(item, "values")
                if not values or values[0] in ("error", "loading", ""): continue
                vid = values[0]
                mark = "☑" if vid in checked else "☐"
                self._apply_mark(item, mark)

    def select_all(self, select):
        if self.mode == "modpack":
            return
        checked = self.get_current_checked()
        if self.mode == "loader" and self.loader_mode in ("forge", "neoforge"):
            vids = []
            for tree in (self.col_main_tree, self.col_all_tree):
                for item in tree.get_children():
                    if "item" in tree.item(item, "tags"):
                        values = tree.item(item, "values")
                        if values: vids.append(values[0])
            if select: checked.update(vids)
            else: checked.difference_update(vids)
            self.refresh_forge_marks()
            self.update_count()
            return

        vids = []
        for parent in self.tree.get_children():
            for item in self.tree.get_children(parent):
                if "item" not in self.tree.item(item, "tags"): continue
                values = self.tree.item(item, "values")
                if values and values[0] not in ("error", "loading", ""):
                    vids.append(values[0])
        if select: checked.update(vids)
        else: checked.difference_update(vids)
        self.refresh_marks()
        self.update_count()

    def update_count(self):
        if self.mode == "client":
            n = len(self.client_checked)
            text = f"已选：{n}"
        elif self.mode == "server":
            n = sum(len(s) for s in self.server_checked.values())
            text = f"已选：{n}"
        elif self.mode == "modpack":
            n = len(self.modpack["files"]) if self.modpack else 0
            text = f"待下载：{n} 个文件"
        elif self.mode == "installer":
            text = "安装加载器"
        else:
            n = len(self.fabric_checked) + len(self.forge_checked) + len(self.neoforge_checked)
            text = f"已选：{n}"
        self.count_label.config(text=text)
        if getattr(self, "column_dividers", None):
            self.root.after_idle(self._place_column_dividers)

    def on_search_change(self, *args):
        if self._search_after:
            try: self.root.after_cancel(self._search_after)
            except Exception: pass
        self._search_after = self.root.after(250, self.apply_search)

    def apply_search(self):
        self._search_after = None
        if self.mode == "client":
            self.rebuild_client_list()
        elif self.mode == "server":
            self.rebuild_server_list()
        elif self.mode == "modpack":
            self.render_modpack()
        elif self.mode == "installer":
            return
        else:
            if self.loader_mode == "fabric":
                self.rebuild_loader_list()
            else:
                self.rebuild_forge_views()

    # ---------- 数据加载与文件树 ----------

    def load_server_data(self):
        if self._server_loading: return
        self._server_loading = True
        def worker():
            results = {}
            with cf.ThreadPoolExecutor(max_workers=5) as pool:
                futs = {pid: pool.submit(fn) for pid, fn in SERVER_FETCHERS.items()}
                for pid, fut in futs.items():
                    try: results[pid] = fut.result()
                    except Exception as e: results[pid] = {"__error__": str(e)}
            self.queue.put(("server_data", results))
        threading.Thread(target=worker, daemon=True).start()

    def on_server_data(self, results):
        self.server_third = results
        self.server_data_loaded = True
        self._server_loading = False
        if self.mode == "server": self.rebuild_server_list()

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir_var.get() or os.getcwd())
        if d:
            self.out_dir_var.set(d)
            self.refresh_filetree()

    def refresh_filetree(self):
        self.filetree.delete(*self.filetree.get_children())
        path = self.out_dir_var.get().strip()
        if not path or not os.path.isdir(path): return
        self._add_dir_items(self.filetree, "", path, depth=0)

    def _add_dir_items(self, tree, parent, path, depth):
        if depth > 3: return
        try:
            entries = sorted(os.listdir(path), key=lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower()))
        except OSError: return
        for name in entries:
            full = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            try: size = human(os.path.getsize(full)) if os.path.isfile(full) else ""
            except OSError: size = ""
            node = tree.insert(parent, "end", text=name, values=(size,))
            if is_dir: self._add_dir_items(tree, node, full, depth + 1)

    def toggle_priority(self):
        self.priority_source = "bmclapi" if self.priority_source == "mojang" else "mojang"
        self.update_priority_btn()
        if self.mode == "client":
            self.rebuild_client_list()
        elif self.mode == "server":
            self.rebuild_server_list()

    # ---------- 下载控制 ----------

    def start_download(self):
        out = self.out_dir_var.get().strip()
        if not out:
            messagebox.showerror("错误", "请先选择下载路径")
            return
        try: os.makedirs(out, exist_ok=True)
        except OSError as e:
            messagebox.showerror("错误", f"无法创建下载目录：\n{e}")
            return

        if self.mode == "modpack":
            if not self.modpack:
                self.set_pack_status("还没有选择整合包", error=True)
                return
            self.start_modpack_install(self._active_concurrency())
            return

        if self.mode == "client":
            vids = [v for v in self.client_checked if v in self.versions]
            if not vids: return messagebox.showinfo("提示", "请先勾选要下载的版本")
            tasks = [("client", vid, self.versions[vid]) for vid in sorted(vids)]
        elif self.mode == "server":
            tasks = self.build_server_tasks()
            if not tasks: return messagebox.showinfo("提示", "请先勾选要下载的服务端版本")
        else:
            tasks = self.build_loader_tasks()
            if not tasks: return messagebox.showinfo("提示", "请先勾选要下载的加载器版本")

        if self.submit_tasks(tasks):
            # 下载已交给后台调度，勾选立刻清空，释放界面以便继续选择下一批
            self._clear_current_checked()

    def build_server_tasks(self):
        tasks = []
        for pid, vid_set in self.server_checked.items():
            if not vid_set: continue
            if pid == "vanilla":
                for vid in sorted(vid_set):
                    if vid in self.versions:
                        tasks.append(("server_vanilla", vid, self.versions[vid]))
            else:
                data = self.server_third.get(pid, {})
                if "__error__" in data: continue
                for vid in sorted(vid_set):
                    if vid in data:
                        tasks.append((f"server_{pid}", vid, data[vid]))
        return tasks

    def build_loader_tasks(self):
        tasks = []
        if self.loader_mode == "fabric":
            for v in sorted(self.fabric_checked):
                tasks.append(("loader_fabric", v, {"ext": self.fabric_ext}))
        elif self.loader_mode == "forge":
            for v in sorted(self.forge_checked):
                tasks.append(("loader_forge", v, {}))
        elif self.loader_mode == "neoforge":
            for v in sorted(self.neoforge_checked):
                tasks.append(("loader_neoforge", v, {}))
        return tasks

    def stop_download(self):
        if not self.downloading:
            return
        self.stop_event.set()
        self.stop_btn.config(state=DISABLED, text="正在停止...")
        with self.download_lock:
            removed_tasks = list(self.download_queue)
            self.download_queue.clear()
            self._outstanding = max(0, self._outstanding - len(removed_tasks))
            for task in removed_tasks:
                self._active_vids.discard(task.get("vid"))
        self._check_complete()

    def _active_concurrency(self):
        try:
            value = int(self.concurrency_var.get())
        except (TypeError, ValueError):
            value = 4
        return max(1, min(32, value))

    def _clear_current_checked(self):
        if self.mode == "client":
            self.client_checked.clear()
            self.rebuild_client_list()
        elif self.mode == "server":
            pid = self.server_provider_var.get()
            self.server_checked.setdefault(pid, set()).clear()
            self.rebuild_server_list()
        elif self.mode == "loader":
            if self.loader_mode == "fabric":
                self.fabric_checked.clear()
                self.rebuild_loader_list()
            elif self.loader_mode == "forge":
                self.forge_checked.clear()
                self.rebuild_forge_views()
            else:
                self.neoforge_checked.clear()
                self.rebuild_forge_views()
        self.update_count()

    def submit_tasks(self, tasks):
        """把一批任务交给后台调度：立即返回，界面可继续选下一批。"""
        pending = [task for task in tasks if task[1] not in self._active_vids]
        if not pending:
            messagebox.showinfo("提示", "这些版本已在下载队列中")
            return False
        if not self.downloading:
            for widget in self.progress_inner.winfo_children():
                widget.destroy()
            self.task_widgets = {}
            self.task_got = {}
            self.total_size = 0
            self.total_progress_var.set(0.0)
            self.total_label.config(text="0%")
            self.downloading = True
        self.stop_event.clear()
        self._download_out_dir = self.out_dir_var.get().strip()
        self._target_workers = self._active_concurrency()
        for _kind, vid, _info in pending:
            frame = tb.Frame(self.progress_inner)
            frame.pack(fill=X, pady=3, padx=2)
            tb.Label(frame, text=vid, width=30, anchor=W, font=FONT_MONO).pack(side=LEFT)
            src_label = tb.Label(frame, text="-", width=22, bootstyle="secondary", font=FONT_SMALL)
            src_label.pack(side=LEFT)
            pbar = tb.Progressbar(frame, maximum=100, bootstyle="info-striped")
            pbar.pack(side=LEFT, fill=X, expand=True, padx=6)
            label = tb.Label(frame, text="等待解析", width=36, anchor=W, font=FONT_MONO, bootstyle=SECONDARY)
            label.pack(side=LEFT)
            self.task_widgets[vid] = {"pbar": pbar, "label": label, "src_label": src_label}
            self.task_got.setdefault(vid, 0)
            self._active_vids.add(vid)
        with self.download_lock:
            self._outstanding += len(pending)
            self._preparing += 1
        self.stop_btn.config(state=NORMAL, text="停止下载")
        threading.Thread(
            target=self._prepare_batch,
            args=(pending, self.priority_source),
            daemon=True,
        ).start()
        self.update_total_progress()
        return True

    def _prepare_one(self, kind, vid, info, priority_source, fallback_source):
        if kind == "client":
            return self.prepare_client(vid, priority_source, fallback_source)
        if kind == "server_vanilla":
            return self.prepare_vanilla_server(vid, priority_source, fallback_source)
        if kind.startswith("server_"):
            return self.prepare_third_server(kind[len("server_"):], vid, info)
        if kind == "loader_fabric":
            return self.prepare_fabric(vid, info)
        if kind == "loader_forge":
            return self.prepare_forge(vid)
        if kind == "loader_neoforge":
            return self.prepare_neoforge(vid)
        if kind == "modpack":
            return self.prepare_modpack_file(vid)
        if kind == "modpack_extra":
            return self.prepare_modpack_extra(vid, info)
        return {"vid": vid, "error": f"未知类型 {kind}"}

    def _prepare_batch(self, tasks, priority_source):
        fallback_source = "bmclapi" if priority_source == "mojang" else "mojang"

        def prepare(task):
            kind, vid, info = task
            if self.stop_event.is_set():
                return {"vid": vid, "cancelled": True}
            try:
                return self._prepare_one(kind, vid, info, priority_source, fallback_source)
            except Exception as e:
                return {"vid": vid, "error": str(e)}

        workers = min(8, max(1, len(tasks)))
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(prepare, tasks):
                vid = result.get("vid")
                if result.get("cancelled") or self.stop_event.is_set():
                    self.queue.put(("task_cancelled", vid))
                elif result.get("skip"):
                    self.queue.put(("task_source", vid, "已存在"))
                    self.queue.put(("task_done", vid, True, "已存在"))
                elif "error" in result:
                    self.queue.put(("task_done", vid, False, result["error"]))
                else:
                    with self.download_lock:
                        self.download_queue.append(result)
                    self.queue.put(("total_size_add", result["candidates"][0]["size"]))
                    self.queue.put(("task_source", vid, result["candidates"][0]["source"]))
        with self.download_lock:
            self._preparing -= 1
        self._ensure_workers()
        self.queue.put(("worker_tick",))

    def _ensure_workers(self):
        with self.download_lock:
            if self.stop_event.is_set():
                return
            while self.active_workers < self._target_workers and self.download_queue:
                self.active_workers += 1
                threading.Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self):
        while True:
            with self.download_lock:
                if self.stop_event.is_set() or not self.download_queue:
                    break
                # 并发数调小后，多余线程跑完当前任务就退出
                if self.active_workers > self._target_workers:
                    break
                task = self.download_queue.popleft()
            self.download_one_task(task)
        with self.download_lock:
            self.active_workers -= 1
        self.queue.put(("worker_tick",))

    def _check_complete(self):
        if not self.downloading:
            return
        with self.download_lock:
            active = self.active_workers
            pending = len(self.download_queue)
            preparing = self._preparing
        if self._outstanding > 0 or preparing > 0 or active > 0 or pending > 0:
            return
        self.downloading = False
        self.stop_btn.config(state=DISABLED, text="停止下载")
        stopped = self.stop_event.is_set()
        self._cleanup_part_files()
        self.refresh_filetree()
        if self.total_size:
            done = sum(self.task_got.values())
            self.total_progress_var.set(min(done * 100.0 / self.total_size, 100.0))
        elif not stopped:
            self.total_progress_var.set(100.0)
        if stopped:
            if self._modpack_installing:
                self._modpack_installing = False
                self.set_pack_status("已停止：已下载完成的文件保留，未完成的缓存已清理", error=True)
            messagebox.showinfo("已停止", "下载已停止：\n已完整下载的文件已保留，未完成的缓存文件已清理。")
        else:
            messagebox.showinfo("完成", "所有下载任务已结束")
        self.stop_event.clear()

    @staticmethod
    def _candidate(source, url, size=0, sha1=None, sha256=None, filename=None):
        return {
            "source": source, "url": url, "size": size or 0,
            "sha1": sha1, "sha256": sha256, "filename": filename,
        }

    def prepare_fabric(self, vid, info):
        # Fabric 的官方源是 fabricmc 的 Maven，不是 Mojang；BMCLAPI 没有镜像安装器。
        ext = info.get("ext", "jar")
        filename = f"fabric-installer-{vid}.{ext}"
        url = f"{FABRIC_MAVEN_ROOT}/{vid}/{filename}"
        return {
            "vid": vid,
            "path": os.path.join(self._download_out_dir, filename),
            "candidates": [self._candidate(LOADER_OFFICIAL_NAMES["fabric"], url, filename=filename)],
        }

    def prepare_forge(self, vid):
        # 官方优先，BMCLAPI 镜像回退；老版本没有 installer，只有 universal jar。
        roots = [(FORGE_OFFICIAL_ROOT, LOADER_OFFICIAL_NAMES["forge"]),
                 (FORGE_MIRROR_ROOT, MIRROR_NAME)]
        if self.priority_source == "bmclapi":
            roots.reverse()
        candidates = []
        for root, source in roots:
            for suffix in ("installer", "universal"):
                filename = f"forge-{vid}-{suffix}.jar"
                candidates.append(self._candidate(
                    source, f"{root}/{vid}/{filename}", filename=filename
                ))
        return {
            "vid": vid,
            "path": os.path.join(self._download_out_dir, f"forge-{vid}-installer.jar"),
            "candidates": candidates,
        }

    def prepare_neoforge(self, vid):
        roots = [(NEOFORGE_OFFICIAL_ROOT, LOADER_OFFICIAL_NAMES["neoforge"]),
                 (NEOFORGE_MIRROR_ROOT, MIRROR_NAME)]
        if self.priority_source == "bmclapi" or not host_reachable(
            f"{NEOFORGE_OFFICIAL_ROOT}/{vid}/neoforge-{vid}-installer.jar"
        ):
            roots.reverse()
        filename = f"neoforge-{vid}-installer.jar"
        candidates = [
            self._candidate(source, f"{root}/{vid}/{filename}", filename=filename)
            for root, source in roots
        ]
        return {
            "vid": vid,
            "path": os.path.join(self._download_out_dir, filename),
            "candidates": candidates,
        }

    def prepare_client(self, vid, priority_source, fallback_source):
        info = self.versions[vid]
        sources = [s for s in (priority_source, fallback_source) if s in info["sources"]]
        candidates = []
        last_err = None
        chosen_json = None
        for src in sources:
            try:
                vj = json.loads(http_get(resolve_url(info["sources"][src], src)).decode("utf-8"))
                dl = (vj.get("downloads") or {}).get("client")
                if not dl or not dl.get("url"):
                    last_err = f"{SOURCE_NAMES[src]} 无 client.jar"; continue
                candidates.append({"source": SOURCE_NAMES[src], "url": resolve_url(dl["url"], src), "size": dl.get("size") or 0, "sha1": dl.get("sha1")})
                if chosen_json is None:
                    chosen_json = vj
            except Exception as e: last_err = f"{SOURCE_NAMES[src]}: {e}"
        if not candidates: return {"vid": vid, "error": last_err or "无可用源"}
        version_dir = os.path.join(self._download_out_dir, vid)
        return {
            "vid": vid,
            "path": os.path.join(version_dir, f"{vid}.jar"),
            "candidates": candidates,
            # 每个版本单独一个文件夹：jar + 版本 JSON 放一起，PCL/HMCL 可直接识别
            "version_json": chosen_json,
            "json_path": os.path.join(version_dir, f"{vid}.json"),
        }

    def prepare_vanilla_server(self, vid, priority_source, fallback_source):
        info = self.versions[vid]
        sources = [s for s in (priority_source, fallback_source) if s in info["sources"]]
        candidates = []
        last_err = None
        for src in sources:
            try:
                vj = json.loads(http_get(resolve_url(info["sources"][src], src)).decode("utf-8"))
                dl = (vj.get("downloads") or {}).get("server")
                if not dl or not dl.get("url"):
                    last_err = f"{SOURCE_NAMES[src]} 无 server.jar"; continue
                candidates.append({"source": SOURCE_NAMES[src], "url": resolve_url(dl["url"], src), "size": dl.get("size") or 0, "sha1": dl.get("sha1")})
            except Exception as e: last_err = f"{SOURCE_NAMES[src]}: {e}"
        if not candidates: return {"vid": vid, "error": last_err or "无可用源"}
        return {"vid": vid, "path": os.path.join(self._download_out_dir, f"{vid}-server.jar"), "candidates": candidates}

    def prepare_third_server(self, pid, vid, info):
        candidates = []
        last_err = None

        def add(url, size=0, sha1=None, sha256=None, filename=None):
            if url:
                candidates.append(self._candidate(
                    PROVIDER_NAMES.get(pid, pid), url, size, sha1, sha256, filename
                ))

        try:
            if pid in PAPERLIKE_PROVIDERS:
                payload = json.loads(http_get(
                    f"https://fill.papermc.io/v3/projects/{pid}/versions/{vid}/builds"
                ))
                builds = payload if isinstance(payload, list) else (payload or {}).get("builds", [])
                if not builds:
                    last_err = f"{PROVIDER_NAMES[pid]} 该版本没有可用构建"
                else:
                    stable = [b for b in builds
                              if str(b.get("channel", "")).upper() == "STABLE"]
                    chosen = stable[0] if stable else builds[0]
                    download = (chosen.get("downloads") or {}).get("server:default") or {}
                    url = download.get("url")
                    if not url:
                        last_err = "该构建没有 server:default 下载项"
                    else:
                        add(
                            url,
                            download.get("size") or 0,
                            None,
                            (download.get("checksums") or {}).get("sha256"),
                            download.get("name") or f"{pid}-{vid}.jar",
                        )
            elif pid == "purpur":
                detail = json.loads(http_get(f"https://api.purpurmc.org/v2/purpur/{vid}"))
                build = (detail.get("builds") or {}).get("latest")
                if build:
                    add(
                        f"https://api.purpurmc.org/v2/purpur/{vid}/{build}/download",
                        filename=f"purpur-{vid}-{build}.jar",
                    )
                else:
                    last_err = "Purpur 该版本没有可用构建"
            elif pid == "leaves":
                rel = json.loads(http_get(
                    f"https://api.github.com/repos/LeavesMC/Leaves/releases/tags/{vid}"
                ))
                jar = next(
                    (a for a in (rel.get("assets") or []) if a.get("name", "").endswith(".jar")),
                    None,
                )
                if jar:
                    add(
                        jar.get("browser_download_url"),
                        jar.get("size") or 0,
                        filename=jar.get("name") or f"leaves-{vid}.jar",
                    )
                else:
                    last_err = "该 Release 没有 jar 附件"
        except Exception as e:
            last_err = str(e)
        if not candidates:
            return {"vid": vid, "error": last_err or "无可用下载"}
        return {
            "vid": vid,
            "path": os.path.join(self._download_out_dir, f"{PROVIDER_NAMES[pid]}-{vid}.jar"),
            "candidates": candidates,
        }

    @staticmethod
    def _file_matches(path, sha1, sha256):
        if not sha1 and not sha256:
            return False
        h1, h256 = hashlib.sha1(), hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h1.update(chunk)
                    h256.update(chunk)
        except OSError:
            return False
        if sha1 and h1.hexdigest().lower() != sha1.lower():
            return False
        if sha256 and h256.hexdigest().lower() != sha256.lower():
            return False
        return True

    def download_one_task(self, task):
        vid, base_path, candidates = task["vid"], task["path"], task["candidates"]
        if self.stop_event.is_set():
            self.queue.put(("task_cancelled", vid)); return
        out_dir = os.path.dirname(base_path)
        first = candidates[0]
        first_path = os.path.join(out_dir, first["filename"]) if first.get("filename") else base_path
        if os.path.exists(first_path) and self._file_matches(
            first_path, first.get("sha1"), first.get("sha256")
        ):
            size = os.path.getsize(first_path)
            self._write_task_version_json(task)
            self.queue.put(("progress", vid, size, size, 0.0))
            self.queue.put(("task_done", vid, True, "已存在")); return
        last_err = None
        for i, cand in enumerate(candidates):
            if self.stop_event.is_set():
                self.queue.put(("task_cancelled", vid)); return
            target = os.path.join(out_dir, cand["filename"]) if cand.get("filename") else base_path
            try:
                if i > 0:
                    self.queue.put(("task_source", vid, cand["source"]))
                    self.queue.put(("progress_msg", vid, f"回退到 {cand['source']}..."))
                else:
                    self.queue.put(("task_source", vid, cand["source"]))
                    self.queue.put(("progress", vid, 0, cand.get("size") or 0, 0.0))
                self._download_url(
                    cand["url"], target, cand.get("size") or 0,
                    cand.get("sha1"), vid, cand.get("sha256"),
                )
                self._write_task_version_json(task)
                self.queue.put(("task_done", vid, True, "完成")); return
            except DownloadCancelled:
                self.queue.put(("task_cancelled", vid)); return
            except Exception as e:
                last_err = e
        self.queue.put(("task_done", vid, False, str(last_err)))

    def _write_task_version_json(self, task):
        """原版 jar 下完后写一份版本 JSON；目录里已经有同名 json 就不动它。"""
        data = task.get("version_json")
        path = task.get("json_path")
        if not data or not path:
            return
        if os.path.exists(path):
            return
        try:
            payload = dict(data)
            payload["id"] = task["vid"]
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _download_url(self, url, path, size, sha1, vid, sha256=None):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".part"
        req = urllib.request.Request(safe_url(url), headers={"User-Agent": UA})
        digest = hashlib.sha1()
        digest256 = hashlib.sha256() if sha256 else None
        got = 0
        total = size or 0
        t_start = time.time()
        last_report = 0.0
        samples = collections.deque(maxlen=32)
        samples.append((t_start, 0))
        try:
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as f:
                if not total: total = int(resp.headers.get("Content-Length") or 0)
                while True:
                    if self.stop_event.is_set(): raise DownloadCancelled("用户停止")
                    chunk = resp.read(64 * 1024)
                    if not chunk: break
                    f.write(chunk)
                    digest.update(chunk)
                    if digest256 is not None: digest256.update(chunk)
                    got += len(chunk)
                    now = time.time()
                    samples.append((now, got))
                    while len(samples) > 2 and now - samples[0][0] > 1.0: samples.popleft()
                    if now - last_report >= 0.15:
                        last_report = now
                        speed = 0.0
                        if len(samples) >= 2:
                            dt = samples[-1][0] - samples[0][0]
                            db = samples[-1][1] - samples[0][1]
                            if dt > 0.05 and db >= 0: speed = db / dt
                        if speed <= 0: speed = got / max(now - t_start, 0.001)
                        self.queue.put(("progress", vid, got, total, speed))
            if self.stop_event.is_set(): raise DownloadCancelled("用户停止")
            if sha1 and digest.hexdigest().lower() != sha1.lower():
                raise RuntimeError("SHA1 校验失败")
            if sha256 and digest256 is not None and digest256.hexdigest().lower() != sha256.lower():
                raise RuntimeError("SHA256 校验失败")
            os.replace(tmp, path)
            self.queue.put(("progress", vid, got, total or got, 0.0))
        except BaseException:
            try:
                if os.path.exists(tmp): os.remove(tmp)
            except OSError: pass
            raise

    def _cleanup_part_files(self):
        out = self.out_dir_var.get().strip()
        if not out or not os.path.isdir(out): return
        try:
            for root_dir, _dirs, files in os.walk(out):
                for name in files:
                    if name.endswith(".part"):
                        try: os.remove(os.path.join(root_dir, name))
                        except OSError: pass
        except OSError: pass

    # ---------- 消息循环 ----------

    def poll_queue(self):
        if getattr(self, "_closing", False):
            return
        # 单条消息出错不能让整个消息循环停摆，否则后续队列消息再也不会被处理。
        try:
            while True:
                message = self.queue.get_nowait()
                try:
                    self.handle_message(message)
                except Exception:
                    log_exception("handle_message")
        except queue.Empty:
            pass
        finally:
            if not getattr(self, "_closing", False):
                try:
                    self._poll_job = self.root.after(60, self.poll_queue)
                except Exception:
                    pass

    def handle_message(self, msg):
        kind = msg[0]
        if kind == "client_manifests": self.on_client_manifests(msg[1])
        elif kind == "server_data": self.on_server_data(msg[1])
        elif kind == "fabric_versions": self.on_fabric_versions(msg[1], msg[2] if len(msg) > 2 else None)
        elif kind == "forge_data": self.on_forge_data(msg[1], msg[2] if len(msg) > 2 else None)
        elif kind == "neoforge_data": self.on_neoforge_data(msg[1], msg[2] if len(msg) > 2 else None)
        elif kind == "modpack_ready": self.on_modpack_ready(msg[1])
        elif kind == "modpack_error": self.on_modpack_error(msg[1])
        elif kind == "modpack_version_json": self.on_modpack_version_json(msg[1], msg[2])
        elif kind == "task_source":
            w = self.task_widgets.get(msg[1])
            if w: w["src_label"].config(text=msg[2])
        elif kind == "progress":
            _, vid, got, total, speed = msg
            self.task_got[vid] = got
            w = self.task_widgets.get(vid)
            if w:
                if total:
                    w["pbar"]["value"] = min(got * 100.0 / total, 100.0)
                    w["label"].config(text=f"{human(got)} / {human(total)}   {human(speed)}/s")
                else: w["label"].config(text=f"{human(got)}   {human(speed)}/s")
            self.update_total_progress()
        elif kind == "progress_msg":
            w = self.task_widgets.get(msg[1])
            if w: w["label"].config(text=msg[2])
        elif kind == "task_cancelled":
            vid = msg[1]
            w = self.task_widgets.get(vid)
            if w: w["label"].config(text="已取消", bootstyle=WARNING)
            self._mark_task_finished(vid)
        elif kind == "task_done":
            _, vid, success, text = msg
            if not success and self._modpack_installing:
                if vid in getattr(self, "_modpack_extra_vids", set()):
                    self._modpack_extra_failures += 1
                else:
                    self.modpack_failures += 1
            w = self.task_widgets.get(vid)
            if w:
                if success:
                    w["pbar"]["value"] = 100
                    label = "已存在" if text == "已存在" else "完成"
                    w["label"].config(text=label, bootstyle=SUCCESS)
                else: w["label"].config(text=f"失败：{text[:36]}", bootstyle=DANGER)
            self._mark_task_finished(vid)
        elif kind == "total_size_add":
            self.total_size += msg[1]
            self.update_total_progress()
        elif kind == "worker_tick":
            self._ensure_workers()
            self._check_complete()
        elif kind == "loader_versions":
            self.on_loader_versions(msg[1], msg[2], msg[3])
        elif kind == "loader_install_done":
            self.on_loader_install_done(msg[1], msg[2], msg[3], msg[4])

    def _mark_task_finished(self, vid):
        if vid in self._active_vids:
            self._active_vids.discard(vid)
        self._outstanding = max(0, self._outstanding - 1)
        if self._modpack_installing and vid in getattr(self, "_modpack_vids", set()):
            self._modpack_remaining = max(0, self._modpack_remaining - 1)
            if self._modpack_remaining == 0 and not self.modpack_finished:
                self.finish_modpack()
        self._check_complete()

    def update_total_progress(self):
        done = sum(self.task_got.values())
        total = self.total_size
        if total:
            pct = min(done * 100.0 / total, 100.0)
            self.total_progress_var.set(pct)
            self.total_label.config(text=f"{pct:5.1f}%   {human(done)} / {human(total)}")
        else: self.total_label.config(text=human(done))

    def on_close(self):
        try:
            drop = getattr(self, "file_drop", None)
            if drop:
                drop.close()
        except Exception:
            pass
        if self.downloading:
            if not messagebox.askyesno("确认", "仍有下载任务进行中，确定退出吗？"): return
            self.stop_event.set()
        for job in (getattr(self, "_poll_job", None), self._search_after,
                    getattr(self, "_divider_job", None)):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self._closing = True
        self.root.quit()
        self.root.destroy()


def main():
    hide_console()
    try:
        root = tb.Window(themename="cosmo")
        apply_window_icon(root)
        MCDownloaderApp(root)
        root.mainloop()
    except Exception:
        log_exception("main")


if __name__ == "__main__":
    main()