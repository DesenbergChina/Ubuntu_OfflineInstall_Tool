'''
Author: Desenberg
Date: 2026-04-21 15:37:43
LastEditors: Desenberg
Description: 通过 SSH 连接到远端 Ubuntu，获取依赖包下载链接，下载到本地，再上传到远端执行离线安装
Copyright (c) 2026 by Desenberg, All Rights Reserved.
'''
# ===================== Windows → Ubuntu XRDP 离线安装工具 =====================
# 依赖：Python 3.6+，需配置系统 SSH 客户端和 SCP 命令才能使用
# ========================================================================

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
import subprocess
import os
import re
import locale
import threading
import urllib.request
import urllib.error
import urllib.parse
import sys
import shlex
import time
import json


# ===================== Stderr 过滤器（消除 Tk/libpng 噪声） =====================
class _StderrFilter:
    """过滤已知 libpng 噪声警告，其他 stderr 原样输出。

    作用：Tkinter 在某些系统上会输出 libpng 的无关警告信息，这个过滤器可以选择性地屏蔽这些噪声，
         同时保留其他真正的错误信息，使日志输出更清洁。
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, data):
        if "libpng warning: tRNS: invalid with alpha channel" in data:
            return
        self._wrapped.write(data)

    def flush(self):
        self._wrapped.flush()


sys.stderr = _StderrFilter(sys.stderr)

# 某些 Tk/libpng 警告来自 C 层，直接写入进程 stderr(fd=2)，不会经过 Python 的 sys.stderr。
# 这里把进程级 stderr 重定向到 NUL，彻底屏蔽该类噪声输出。
_DEVNULL_STDERR_HANDLE = None
try:
    _DEVNULL_STDERR_HANDLE = open(os.devnull, "w", encoding="utf-8")
    os.dup2(_DEVNULL_STDERR_HANDLE.fileno(), 2)
except OSError:
    pass

# ===================== 全局配置常量 =====================
# 用户需要在系统 SSH 配置中创建对应的快捷名称（~/.ssh/config）
DEFAULT_SSH_ALIAS = "123"  # 默认 SSH 快捷名称，对应远端 Ubuntu 系统
# 离线包目录的前缀，最后生成的目录名形如：offline_bundle_
DEFAULT_BUNDLE_DIR_PREFIX = "offline_bundle"
# 可以在该json文件中填写"SSH_ALIAS"和 "BUNDLE_DIR_PREFIX"字段替代默认值
CONFIG_FILE_NAME = "xrdp_offline_config_release.json"  # 保存用户配置的 JSON 文件名


def _app_base_dir():
    # 兼容源码运行与 PyInstaller 打包后的 exe 运行。
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _config_file_path(config_path=CONFIG_FILE_NAME):
    if os.path.isabs(config_path):
        return config_path
    return os.path.join(_app_base_dir(), config_path)


def _load_runtime_config(config_path=CONFIG_FILE_NAME):
    """从配置文件加载运行时配置。

    参数：
        config_path (str): 配置文件路径（绝对或相对）

    返回：
        dict: 包含 SSH_ALIAS 和 BUNDLE_DIR_PREFIX 的字典。若文件不存在或解析失败，返回默认值。
    """
    config_file = _config_file_path(config_path)
    config = {
        "SSH_ALIAS": DEFAULT_SSH_ALIAS,
        "BUNDLE_DIR_PREFIX": DEFAULT_BUNDLE_DIR_PREFIX,
    }

    # 配置文件不存在则返回默认配置
    if not os.path.exists(config_file):
        return config

    # 尝试读取 JSON 配置，若失败则使用默认配置
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return config

    # 从配置文件中提取值，若为空则保留默认值
    ssh_alias = str(data.get("SSH_ALIAS", "")).strip()
    bundle_dir_prefix = str(data.get("BUNDLE_DIR_PREFIX", "")).strip()

    if ssh_alias:
        config["SSH_ALIAS"] = ssh_alias
    if bundle_dir_prefix:
        config["BUNDLE_DIR_PREFIX"] = bundle_dir_prefix

    return config


def _ensure_default_config_file(config_path=CONFIG_FILE_NAME):
    config_file = _config_file_path(config_path)
    if os.path.exists(config_file):
        return

    sample = {
        "SSH_ALIAS": DEFAULT_SSH_ALIAS,
        "BUNDLE_DIR_PREFIX": DEFAULT_BUNDLE_DIR_PREFIX,
    }
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(sample, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError:
        pass


def _save_runtime_config(ssh_alias, bundle_dir_prefix, config_path=CONFIG_FILE_NAME):
    ssh_alias = (ssh_alias or "").strip()
    bundle_dir_prefix = (bundle_dir_prefix or "").strip()
    if not ssh_alias:
        return False, "SSH_ALIAS 不能为空"
    if not bundle_dir_prefix:
        return False, "BUNDLE_DIR_PREFIX 不能为空"

    config_file = _config_file_path(config_path)
    data = {
        "SSH_ALIAS": ssh_alias,
        "BUNDLE_DIR_PREFIX": bundle_dir_prefix,
    }
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return True, config_file
    except OSError as e:
        return False, str(e)


_runtime_config = _load_runtime_config()
SSH_ALIAS = _runtime_config["SSH_ALIAS"]
BUNDLE_DIR_PREFIX = _runtime_config["BUNDLE_DIR_PREFIX"]

PACKAGE_SERVICE_MAP = {
    "xrdp": ["xrdp"],
    "lightdm": ["lightdm"],
    "openssh-server": ["ssh"],
    "nginx": ["nginx"],
    "apache2": ["apache2"],
    "mysql-server": ["mysql"],
    "mariadb-server": ["mariadb"],
    "postgresql": ["postgresql"],
    "redis-server": ["redis-server"],
    "docker.io": ["docker"],
}

CODENAME_RELEASE_MAP = {
    "focal": "20.04",
    "jammy": "22.04",
    "noble": "24.04",
}

# 启用后，下载与 404 刷新仅信任目标发行版(codename)解析结果，避免混入跨版本包。
STRICT_CODENAME_ONLY = True


def decode_output(data):
    if data is None:
        return ""

    encodings = [
        "utf-8",
        locale.getpreferredencoding(False),
        "gbk",
    ]

    for enc in encodings:
        if not enc:
            continue
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")


def run_cmd(args, timeout=20, input_data=None):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            input=input_data,
            timeout=timeout
        )
        stdout = decode_output(result.stdout).strip()
        stderr = decode_output(result.stderr).strip()
        return result.returncode, stdout, stderr
    except Exception as e:
        return 1, "", str(e)


def run_ssh_cmd(command, timeout=20):
    """执行远程 SSH 命令并返回标准输出。

    参数：
        command (str): 要在远端执行的命令字符串
        timeout (int): 命令超时时间（秒），默认 20 秒

    返回：
        tuple: (stdout, stderr) - 命令的标准输出和标准错误输出

    说明：
        使用系统原生 SSH 客户端，需确保已配置 ~/.ssh/config 中 SSH_ALIAS 对应的主机。
    """
    _, stdout, stderr = run_cmd(["ssh", SSH_ALIAS, command], timeout=timeout)
    return stdout, stderr


def run_ssh_cmd_rc(command, timeout=20):
    return run_cmd(["ssh", SSH_ALIAS, command], timeout=timeout)


def run_scp_upload(local_path, remote_dir, timeout=120):
    return run_cmd(["scp", local_path, f"{SSH_ALIAS}:{remote_dir}"], timeout=timeout)


def run_ssh_cmd_with_input(command, input_text, timeout=20, force_tty=False):
    # 某些环境下 sudo 需要通过伪终端读取口令，这里可按需开启 -tt。
    args = ["ssh"]
    if force_tty:
        args.append("-tt")
    args.extend([SSH_ALIAS, command])

    # 预置两次口令输入，兼容少数场景下 sudo 二次读取 stdin。
    input_bytes = ((input_text + "\n") * 2).encode("utf-8")
    return run_cmd(args, timeout=timeout, input_data=input_bytes)


def verify_sudo_password(sudo_password):
    cmd = "sudo -S -k -p '' true"
    rc, out, err = run_ssh_cmd_with_input(
        cmd, sudo_password, timeout=20, force_tty=True)
    return rc == 0, rc, out, err


def redact_secrets(text, secrets):
    if not text:
        return text

    safe_text = text
    for secret in secrets:
        if secret:
            safe_text = safe_text.replace(secret, "******")
    return safe_text

# ===================== 获取 Ubuntu 系统信息 =====================


def get_ubuntu_info():
    """获取远端 Ubuntu 系统的发行版代号和架构信息。

    返回：
        tuple: (codename, arch, error_msg)
            - codename (str): Ubuntu 发行版代号（如 focal, jammy, noble）
            - arch (str): 系统架构（如 amd64, arm64）
            - error_msg (str): 执行过程中的错误信息（若无则为空）

    说明：
        此信息用于后续筛选兼容的 .deb 包，避免混装不同版本的包。
    """
    codename, err1 = run_ssh_cmd("lsb_release -c | cut -f2")
    arch, err2 = run_ssh_cmd("dpkg --print-architecture")
    return codename.strip(), arch.strip(), (err1 or err2).strip()

# ===================== 获取所有依赖包 =====================


def _extract_deb_uris(text):
    if not text:
        return []

    def _normalize_uri(uri):
        if not uri:
            return ""

        normalized = re.sub(r"\s+", "", uri)
        if normalized.startswith("//"):
            # apt 某些源会输出协议相对地址（//host/path），urllib 需要显式协议。
            return f"http:{normalized}"
        return normalized

    # 兼容 apt 输出中的多种 URI 形式（https/file/本地路径），并允许携带查询参数。
    quoted_uris = re.findall(r"'([^'\n\r]*\.deb(?:\?[^'\n\r]*)?)'", text)
    plain_uris = re.findall(
        r"((?:https?|file)://[^\s'\"]+\.deb(?:\?[^\s'\"]*)?)",
        text,
    )
    local_paths = re.findall(r"(/[^\s'\"]+\.deb)", text)

    candidates = quoted_uris + plain_uris + local_paths
    normalized = [_normalize_uri(u) for u in candidates if u]
    return list(dict.fromkeys(normalized))


def _apt_target_release_arg(codename=""):
    safe_codename = (codename or "").strip()
    if not safe_codename:
        return ""
    return f"-o APT::Default-Release={shlex.quote(safe_codename)}"


def _get_candidate_version(pkg, codename=""):
    release_arg = _apt_target_release_arg(codename)
    # 优先使用 madison，输出字段稳定，避免依赖 policy 的本地化文本。
    rc, out, _ = run_ssh_cmd_rc(
        f"apt-cache {release_arg} madison {shlex.quote(pkg)} | awk 'NF>=3 {{print $3; exit}}'".strip(
        ),
        timeout=20,
    )
    if rc == 0 and out.strip():
        candidate = out.strip().splitlines()[0].strip()
        if candidate and candidate != "(none)":
            return candidate

    # 兜底：从 show 输出里取第一个可见版本。
    rc, out, _ = run_ssh_cmd_rc(
        f"apt-cache {release_arg} show {shlex.quote(pkg)} 2>/dev/null | sed -n 's/^Version: //p' | head -n 1".strip(),
        timeout=20,
    )
    if rc == 0 and out.strip():
        candidate = out.strip().splitlines()[0].strip()
        if candidate and candidate != "(none)":
            return candidate
    return ""


def _extract_unavailable_packages(err_text):
    if not err_text:
        return []

    missing = []
    for line in err_text.splitlines():
        text = line.strip()
        if not text:
            continue

        # 兼容英文/中文 apt 报错：Unable to locate package / 无法定位软件包。
        m = re.search(r"Unable to locate package\s+(.+)$",
                      text, flags=re.IGNORECASE)
        if not m:
            m = re.search(r"无法定位软件包\s+(.+)$", text)
        if m:
            pkg = m.group(1).strip().strip("'\"")
            if pkg:
                missing.append(pkg)
    return list(dict.fromkeys(missing))


def _is_filename_compatible_with_codename(filename, codename):
    codename_series = CODENAME_RELEASE_MAP.get((codename or "").lower(), "")
    if not codename_series:
        return True

    version = _version_from_deb_filename(filename)
    if not version:
        return True

    # 能识别发行版序列时严格匹配；无法识别时保守放行，避免误杀通用包。
    series = _extract_release_series_from_version(version)
    return (not series) or (series == codename_series)


def _filter_urls_by_codename(urls, codename):
    if not codename:
        return urls, []

    filtered = []
    rejected = []
    for url in urls:
        name = _filename_from_url(url)
        if _is_filename_compatible_with_codename(name, codename):
            filtered.append(url)
        else:
            rejected.append(name)
    return filtered, rejected


def get_deb_urls(packages, include_recommends=False, codename=""):
    """通过远端 apt-get 获取指定包的下载链接。

    参数：
        packages (list): 包名列表，如 ['xrdp', 'lightdm']
        include_recommends (bool): 是否包含推荐的依赖包，默认 False（仅必需依赖）
        codename (str): Ubuntu 发行版代号，用于严格版本匹配

    返回：
        tuple: (urls, error_msg)
            - urls (list): .deb 下载链接列表
            - error_msg (str): 若无法获取链接，返回错误描述

    说明：
        1. 首先尝试 apt-get install --print-uris 获取链接
        2. 若失败则尝试 404 刷新（从镜像目录扫描新版本）
        3. 最后回退到 apt-get download --print-uris（逐包获取）
    """
    if not packages:
        return [], "未提供待安装包名"

    recommend_arg = "" if include_recommends else "--no-install-recommends"
    release_arg = _apt_target_release_arg(codename)
    pkg_args = " ".join(shlex.quote(pkg) for pkg in packages)
    cmd = f"apt-get {release_arg} --print-uris --yes --reinstall install {recommend_arg} {pkg_args}".strip(
    )
    rc, out, err = run_ssh_cmd_rc(cmd, timeout=60)

    # apt-get --print-uris 在不同镜像/环境下输出格式不完全一致，这里统一做宽松提取。
    urls = _extract_deb_uris(out)

    # 去重并保持顺序
    unique_urls = list(dict.fromkeys(urls))
    if unique_urls:
        filtered_urls, rejected_names = _filter_urls_by_codename(
            unique_urls, codename)
        if filtered_urls:
            warn = err.strip()
            if rejected_names:
                reject_summary = "，".join(rejected_names[:5])
                if len(rejected_names) > 5:
                    reject_summary += " ..."
                extra = f"已过滤疑似跨发行版包: {reject_summary}"
                warn = f"{warn} | {extra}" if warn else extra
            return filtered_urls, warn
        if rejected_names:
            return [], f"检测到非 {codename} 版本包，已全部拒绝：{', '.join(rejected_names[:5])}"
        return [], err.strip()

    # 若包含不可用包（如某些发行版不存在 pulseaudio-module-xrdp），
    # 则自动剔除后重试，避免单个包导致整批依赖解析失败。
    unavailable_pkgs = _extract_unavailable_packages(err or "")
    if unavailable_pkgs:
        available_pkgs = [
            pkg for pkg in packages if pkg not in unavailable_pkgs]
        if not available_pkgs:
            return [], f"请求的包均不可用：{' '.join(unavailable_pkgs)}"

        available_args = " ".join(shlex.quote(pkg) for pkg in available_pkgs)
        retry_missing_cmd = (
            f"apt-get {release_arg} --print-uris --yes --reinstall install {recommend_arg} {available_args}"
        ).strip()
        _, retry_missing_out, retry_missing_err = run_ssh_cmd_rc(
            retry_missing_cmd, timeout=60)
        retry_missing_urls = _extract_deb_uris(retry_missing_out)
        retry_missing_unique = list(dict.fromkeys(retry_missing_urls))
        if retry_missing_unique:
            retry_filtered, retry_rejected = _filter_urls_by_codename(
                retry_missing_unique, codename)
            if retry_filtered:
                ignore_msg = (
                    f"已自动忽略不可用包: {', '.join(unavailable_pkgs)}"
                )
                warn = retry_missing_err.strip()
                if retry_rejected:
                    reject_summary = "，".join(retry_rejected[:5])
                    if len(retry_rejected) > 5:
                        reject_summary += " ..."
                    extra = f"已过滤疑似跨发行版包: {reject_summary}"
                    warn = f"{warn} | {extra}" if warn else extra
                warn = f"{ignore_msg} | {warn}" if warn else ignore_msg
                return retry_filtered, warn

    # 如果已安装版本在当前源中不存在（常见于混源后清理），改用 Candidate 版本并允许降级重试。
    missing_source_error = "Can't find a source to download version"
    if missing_source_error in (err or ""):
        # 先去掉 --reinstall 重试，让 apt 可以回退到仓库中的可用候选版本。
        retry_no_reinstall_cmd = (
            f"apt-get {release_arg} --print-uris --yes install {recommend_arg} {pkg_args}"
        ).strip()
        _, retry_no_reinstall_out, retry_no_reinstall_err = run_ssh_cmd_rc(
            retry_no_reinstall_cmd, timeout=90)
        retry_no_reinstall_urls = _extract_deb_uris(retry_no_reinstall_out)
        retry_no_reinstall_unique = list(
            dict.fromkeys(retry_no_reinstall_urls))
        if retry_no_reinstall_unique:
            retry_filtered, retry_rejected = _filter_urls_by_codename(
                retry_no_reinstall_unique, codename)
            if retry_filtered:
                warn = retry_no_reinstall_err.strip()
                if retry_rejected:
                    reject_summary = "，".join(retry_rejected[:5])
                    if len(retry_rejected) > 5:
                        reject_summary += " ..."
                    extra = f"已过滤疑似跨发行版包: {reject_summary}"
                    warn = f"{warn} | {extra}" if warn else extra
                return retry_filtered, warn

        pkg_specs = []
        unresolved = []
        for pkg in packages:
            candidate = _get_candidate_version(pkg, codename=codename)
            if candidate:
                pkg_specs.append(f"{pkg}={candidate}")
            else:
                unresolved.append(pkg)

        if pkg_specs:
            spec_args = " ".join(shlex.quote(spec) for spec in pkg_specs)
            retry_cmd = (
                f"apt-get {release_arg} --print-uris --yes --reinstall --allow-downgrades "
                f"install {recommend_arg} {spec_args}"
            ).strip()
            _, retry_out, retry_err = run_ssh_cmd_rc(retry_cmd, timeout=90)
            retry_urls = _extract_deb_uris(retry_out)
            retry_unique_urls = list(dict.fromkeys(retry_urls))
            if retry_unique_urls:
                retry_filtered, retry_rejected = _filter_urls_by_codename(
                    retry_unique_urls, codename)
                if retry_filtered:
                    warn = retry_err.strip()
                    if retry_rejected:
                        reject_summary = "，".join(retry_rejected[:5])
                        if len(retry_rejected) > 5:
                            reject_summary += " ..."
                        extra = f"已过滤疑似跨发行版包: {reject_summary}"
                        warn = f"{warn} | {extra}" if warn else extra
                    return retry_filtered, warn

    # 回退：若 install --print-uris 未返回链接，尝试逐包 download --print-uris。
    fallback_urls = []
    fallback_errors = []
    for pkg in packages:
        dl_cmd = f"apt-get {release_arg} --print-uris download {shlex.quote(pkg)}".strip(
        )
        rc_dl, out_dl, err_dl = run_ssh_cmd_rc(dl_cmd, timeout=40)
        extracted = _extract_deb_uris(out_dl)
        if extracted:
            fallback_urls.extend(extracted)
        if rc_dl != 0 and err_dl.strip():
            fallback_errors.append(f"{pkg}: {err_dl.strip()}")

    fallback_urls = list(dict.fromkeys(fallback_urls))
    if fallback_urls:
        fallback_filtered, fallback_rejected = _filter_urls_by_codename(
            fallback_urls, codename)
        if fallback_filtered:
            warn = ""
            if fallback_rejected:
                reject_summary = "，".join(fallback_rejected[:5])
                if len(fallback_rejected) > 5:
                    reject_summary += " ..."
                warn = f"已过滤疑似跨发行版包: {reject_summary}"
            return fallback_filtered, warn

    details = []
    if rc != 0:
        details.append(f"apt-get 返回码: {rc}")
    if err.strip():
        details.append(err.strip())
    if fallback_errors:
        details.append(" | ".join(fallback_errors))
    elif out.strip():
        details.append("命令有输出但未解析到 .deb 链接，请检查 apt 输出格式")

    return unique_urls, " | ".join(details).strip()

# ===================== 生成官方下载链接 =====================


def make_package_names(deb_urls):
    names = []
    for url in deb_urls:
        filename = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        names.append(filename)
    return names


def _filename_from_url(url):
    return urllib.parse.unquote(url.rsplit("/", 1)[-1])


def _package_name_from_deb_filename(filename):
    # Debian 包文件命名通常为 name_version_arch.deb，这里取 name 部分。
    return filename.split("_", 1)[0] if "_" in filename else ""


def _arch_from_deb_filename(filename):
    parts = filename.rsplit("_", 1)
    if len(parts) != 2 or not parts[1].endswith(".deb"):
        return ""
    return parts[1][:-4]


def _version_from_deb_filename(filename):
    pkg = _package_name_from_deb_filename(filename)
    arch = _arch_from_deb_filename(filename)
    if not pkg or not arch:
        return ""

    prefix = f"{pkg}_"
    suffix = f"_{arch}.deb"
    if not filename.startswith(prefix) or not filename.endswith(suffix):
        return ""
    return filename[len(prefix):-len(suffix)]


def _deb_identity_from_filename(filename):
    # 用“包名+架构”标识同一依赖，忽略版本号，兼容 404 刷新到新版本文件名。
    pkg = _package_name_from_deb_filename(filename)
    arch = _arch_from_deb_filename(filename)
    if not pkg:
        return ""
    return f"{pkg}::{arch}" if arch else pkg


def _deb_identity_set_from_filenames(filenames):
    identities = set()
    for name in filenames:
        identity = _deb_identity_from_filename(name)
        if identity:
            identities.add(identity)
    return identities


def _natural_version_key(version):
    tokens = re.split(r"(\d+)", version)
    key = []
    for token in tokens:
        if not token:
            continue
        if token.isdigit():
            key.append((0, int(token)))
        else:
            key.append((1, token))
    return key


def _extract_release_series_from_version(version):
    if not version:
        return ""

    # 例如：2ubuntu1.7~22.04.14 -> 22.04
    match_tilde = re.search(r"~(\d{2}\.\d{2})", version)
    if match_tilde:
        return match_tilde.group(1)

    # 例如：1ubuntu2.22.04.1 -> 22.04
    match_ubuntu = re.search(r"ubuntu\d+\.(\d{2}\.\d{2})(?:\.|$)", version)
    if match_ubuntu:
        return match_ubuntu.group(1)

    return ""


def _is_release_compatible(expected_filename, candidate_filename, codename):
    # 先确保同一包同一架构。
    if _deb_identity_from_filename(expected_filename) != _deb_identity_from_filename(candidate_filename):
        return False

    expected_ver = _version_from_deb_filename(expected_filename)
    candidate_ver = _version_from_deb_filename(candidate_filename)
    expected_series = _extract_release_series_from_version(expected_ver)
    candidate_series = _extract_release_series_from_version(candidate_ver)

    codename_series = CODENAME_RELEASE_MAP.get((codename or "").lower(), "")
    target_series = expected_series or codename_series
    if not target_series:
        return True

    # 若期望版本明确带系列信息，候选也必须带并且一致。
    if expected_series and not candidate_series:
        return False

    if candidate_series and candidate_series != target_series:
        return False

    # 无法解析发行版序列时，至少约束同一上游主版本，避免 22.x 误升到 24.x。
    if codename and not expected_series and not candidate_series:
        expected_ver = _version_from_deb_filename(expected_filename)
        candidate_ver = _version_from_deb_filename(candidate_filename)

        def _upstream_major(version):
            if not version:
                return None
            plain = version.split(":", 1)[-1].split("-", 1)[0]
            match = re.match(r"(\d+)", plain)
            if not match:
                return None
            return int(match.group(1))

        expected_major = _upstream_major(expected_ver)
        candidate_major = _upstream_major(candidate_ver)
        if expected_major is not None and candidate_major is not None and expected_major != candidate_major:
            return False

    return True


def _resolve_url_from_pool_listing(stale_url, filename, log_print, codename=""):
    pkg = _package_name_from_deb_filename(filename)
    arch = _arch_from_deb_filename(filename)
    if not pkg or not arch or not stale_url:
        return ""

    base_url = stale_url.rsplit("/", 1)[0] + "/"
    try:
        with urllib.request.urlopen(base_url, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log_print(f"⚠️ 无法读取镜像目录索引：{e}")
        return ""

    pattern = re.compile(
        rf'href="({re.escape(pkg)}_[^"/]+_{re.escape(arch)}\.deb)"',
        flags=re.IGNORECASE,
    )
    candidates = list(dict.fromkeys(pattern.findall(html)))
    if not candidates:
        return ""

    compatible = [
        name for name in candidates
        if _is_release_compatible(filename, name, codename)
    ]
    if not compatible:
        return ""

    newest_name = max(compatible, key=lambda n: _natural_version_key(
        _version_from_deb_filename(n)))
    return urllib.parse.urljoin(base_url, newest_name)


def _resolve_fresh_url_for_filename(filename, log_print, stale_url="", codename=""):
    pkg = _package_name_from_deb_filename(filename)
    best_from_apt = ""
    urls = []

    if pkg:
        urls, err = get_deb_urls(
            [pkg], include_recommends=False, codename=codename)
        if urls:
            # 优先匹配同名包且与当前发行版兼容的 .deb 链接。
            for url in urls:
                candidate_name = _filename_from_url(url)
                if not candidate_name.startswith(f"{pkg}_"):
                    continue
                if _is_release_compatible(filename, candidate_name, codename):
                    best_from_apt = url
                    break
        elif err:
            log_print(f"⚠️ 无法通过 apt 刷新 {pkg} 的下载地址：{err}")

    if best_from_apt and _filename_from_url(best_from_apt) != filename:
        return best_from_apt

    if urls and not best_from_apt:
        log_print(f"⚠️ 已跳过非同发行版候选：{pkg}")

    # 远端离线场景下 apt 可能返回旧版本；此时直接扫描镜像目录取同包同架构较新版本。
    fallback_url = _resolve_url_from_pool_listing(
        stale_url, filename, log_print, codename=codename)
    if fallback_url and _filename_from_url(fallback_url) != filename:
        return fallback_url

    return best_from_apt or fallback_url


def _download_with_retries(url, local_path, filename, log_print, idx, total, max_attempts=4):
    transient_http_codes = {429, 500, 502, 503, 504}

    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                with open(local_path, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
            return True, 0, ""
        except urllib.error.HTTPError as e:
            if e.code in transient_http_codes and attempt < max_attempts:
                wait_seconds = min(2 ** (attempt - 1), 8)
                log_print(
                    f"[{idx}/{total}] 下载遇到 HTTP {e.code}，{wait_seconds} 秒后重试 ({attempt}/{max_attempts})：{filename}"
                )
                time.sleep(wait_seconds)
                continue
            return False, e.code, f"下载失败：{filename}，错误：HTTP {e.code}"
        except urllib.error.URLError as e:
            if attempt < max_attempts:
                wait_seconds = min(2 ** (attempt - 1), 8)
                log_print(
                    f"[{idx}/{total}] 网络异常，{wait_seconds} 秒后重试 ({attempt}/{max_attempts})：{filename}"
                )
                time.sleep(wait_seconds)
                continue
            return False, -1, f"下载失败：{filename}，网络错误：{e}"
        except Exception as e:
            return False, -2, f"下载失败：{filename}，错误：{e}"

    return False, -3, f"下载失败：{filename}，已达到最大重试次数"


def parse_package_input(text):
    if not text:
        return []

    # 支持空格、逗号、分号、换行分隔，过滤空项并去重保序。
    raw_items = re.split(r"[\s,;，；]+", text.strip())
    cleaned = [item for item in raw_items if item]
    return list(dict.fromkeys(cleaned))


def build_service_candidates(packages):
    candidates = []
    for pkg in packages:
        mapped = PACKAGE_SERVICE_MAP.get(pkg, [pkg])
        for svc in mapped:
            if svc and svc not in candidates:
                candidates.append(svc)
    return candidates


def make_post_install_hints(packages):
    hints = []
    if "xrdp" in packages:
        hints.append("XRDP: 默认端口 3389，可用 mstsc 连接")
    if "openssh-server" in packages:
        hints.append("SSH: 确认 22 端口放行并测试 ssh 登录")
    if "nginx" in packages:
        hints.append("Nginx: 可执行 systemctl status nginx 检查服务状态")
    if "apache2" in packages:
        hints.append("Apache2: 可执行 systemctl status apache2 检查服务状态")
    if "docker.io" in packages:
        hints.append("Docker: 可执行 docker --version 与 systemctl status docker")
    if not hints:
        hints.append("建议执行 systemctl --failed 与 journalctl -p err -b 排查异常")
    return hints


def _safe_name_token(text, fallback):
    token = re.sub(r"[^0-9A-Za-z._-]+", "-", (text or "").strip())
    token = re.sub(r"-{2,}", "-", token).strip("-_.")
    return token or fallback


def build_artifact_paths(ssh_alias, packages):
    """构建本地和远端的文件名、目录名等工作路径。

    参数：
        ssh_alias (str): SSH 连接别名，如 'test'
        packages (list): 目标安装包名列表

    返回：
        dict: 包含以下键值：
            - tag: 文件名标签（由 SSH 别名生成）
            - bundle_dir: 本地离线包目录名
            - remote_dir: 远端目录路径（~/ 开头）
            - script_name: 安装脚本文件名
            - version_file: 系统信息文件名
            - package_file: 包清单文件名
            - links_file: 下载链接文件名

    示例：
        若 ssh_alias='test'，生成的 tag 为 'test'，则：
        - bundle_dir: 'offline_bundle_test'
        - version_file: 'ubuntu_version_test.txt'
        - links_file: 'download_links_test.txt'
    """
    ssh_token = _safe_name_token(ssh_alias, "ssh")
    pkg_tokens = [_safe_name_token(pkg, "pkg") for pkg in packages if pkg]
    if not pkg_tokens:
        pkg_tokens = ["pkg"]

    # 控制文件名长度，避免在不同系统上触发路径长度问题。
    max_pkg_tokens = 4
    pkg_part = "-".join(pkg_tokens[:max_pkg_tokens])
    if len(pkg_tokens) > max_pkg_tokens:
        pkg_part += f"-and{len(pkg_tokens) - max_pkg_tokens}"

    # 简化：仅用 SSH 别名作为标签（这样文件名更短更清洁）
    tag = f"{ssh_token}"[:80].rstrip("-_.")
    bundle_dir = f"{BUNDLE_DIR_PREFIX}_{tag}"

    return {
        "tag": tag,
        "bundle_dir": bundle_dir,
        "remote_dir": f"~/{bundle_dir}",
        "script_name": f"install_{tag}.sh",
        "version_file": f"ubuntu_version_{tag}.txt",
        "package_file": f"package_list_{tag}.txt",
        "links_file": f"download_links_{tag}.txt",
    }

# ===================== 保存所有文件到 Windows =====================


def save_files(codename, arch, package_names, links, artifact_paths):
    """将获取到的系统信息、包清单和下载链接保存到本地文件。

    参数：
        codename (str): Ubuntu 发行版代号
        arch (str): 系统架构
        package_names (list): .deb 文件名列表
        links (list): .deb 下载 URL 列表
        artifact_paths (dict): build_artifact_paths() 返回的路径字典

    返回：
        tuple: (version_file, package_file, links_file) - 生成的三个文件路径

    说明：
        生成的文件：
        1. version_file: 记录 Ubuntu 版本和架构，供后续参考
        2. package_file: 列出所有要下载的 .deb 文件名
        3. links_file: 包含所有下载链接，用户可直接用浏览器打开
    """
    version_file = artifact_paths["version_file"]
    package_file = artifact_paths["package_file"]
    links_file = artifact_paths["links_file"]

    # 保存系统版本和架构信息
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(f"版本: {codename}\n架构: {arch}")

    # 保存包文件名清单
    with open(package_file, "w", encoding="utf-8") as f:
        f.write("\n".join(package_names))

    # 保存下载链接（可直接复制到浏览器打开）
    with open(links_file, "w", encoding="utf-8") as f:
        f.write("======== 打开下面链接下载 .deb 文件 ========\n\n")
        f.write("\n".join(links))

    return version_file, package_file, links_file


def build_install_script(bundle_dir, packages, script_name):
    """生成离线安装脚本（install_*.sh）。

    参数：
        bundle_dir (str): 本地离线包目录路径
        packages (list): 目标安装包名列表
        script_name (str): 生成的脚本文件名

    返回：
        str: 生成的脚本文件的完整路径

    说明：
        生成的脚本包含以下功能：
        1. 双重 dpkg -i 安装（处理依赖循环）
        2. 配置 dpkg 状态
        3. 针对 XRDP/Xorg 核心包的离线修复（处理升级中断）
        4. 服务自动启用
        5. XRDP 异常排查提示
    """
    os.makedirs(bundle_dir, exist_ok=True)
    # 根据包名推断应该启用哪些服务，并生成对应的 systemctl 启用代码
    services = build_service_candidates(packages)
    service_block = ""
    if services:
        svc_items = " ".join(shlex.quote(svc) for svc in services)
        service_block = f"""
enable_service_if_exists() {{
    local svc="$1"
    if systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '{{print $1}}' | grep -qx "${{svc}}.service"; then
        sudo systemctl enable --now "$svc" || true
        echo 已尝试启用服务: "$svc"
    fi
}}

for svc in {svc_items}; do
    enable_service_if_exists "$svc"
done
"""

    open_3389 = ""
    if "xrdp" in packages:
        open_3389 = """
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow 3389/tcp || true
fi
"""

    pkg_echo = " ".join(packages)

    # 自动生成 Ubuntu 安装脚本
    install_sh = f"""#!/bin/bash
set -e
echo 开始离线安装，目标包: {pkg_echo}

# 离线修复路径：若检测到 XRDP/Xorg 核心包，先清理坏状态再重装。
if ls ./xserver-xorg-core_*.deb >/dev/null 2>&1 || ls ./xorgxrdp_*.deb >/dev/null 2>&1; then
    echo 检测到 XRDP/Xorg 核心包，执行离线修复预处理...
    sudo dpkg --remove --force-remove-reinstreq xorgxrdp xserver-xorg-core xserver-common 2>/dev/null || true
    sudo dpkg --purge xorgxrdp xserver-xorg-core xserver-common 2>/dev/null || true
fi

for i in 1 2; do
  sudo dpkg -i ./*.deb || true
done

sudo dpkg --configure -a || true

# 等价于在线 apt --reinstall 的离线方案：对核心包做一次定向重装与配置。
if ls ./xserver-common_*.deb >/dev/null 2>&1 && ls ./xserver-xorg-core_*.deb >/dev/null 2>&1; then
    echo 执行 XRDP/Xorg 核心包离线重装...
    sudo dpkg -i ./xserver-common_*.deb ./xserver-xorg-core_*.deb ./xserver-xorg-input-all_*.deb ./xorgxrdp_*.deb ./xrdp_*.deb 2>/dev/null || true
    sudo dpkg --configure -a || true
fi

if dpkg -l | awk '$1 ~ /^(iU|iF|rc)$/ {{print $2}}' | grep -E '^(xserver-common|xserver-xorg-core|xorgxrdp|xrdp)$' >/dev/null 2>&1; then
    echo 警告: 仍有 XRDP/Xorg 核心包未正确配置，请检查版本是否与当前发行版匹配。
fi

{service_block}
{open_3389}

echo
echo ==============================
echo 安装完成！
echo ==============================
"""
    script_path = os.path.join(bundle_dir, script_name)
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(install_sh)
    return script_path


def download_debs(deb_urls, bundle_dir, log_print, codename=""):
    os.makedirs(bundle_dir, exist_ok=True)
    downloaded_files = []

    total = len(deb_urls)
    for idx, url in enumerate(deb_urls, start=1):
        filename = _filename_from_url(url)
        local_path = os.path.join(bundle_dir, filename)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            log_print(f"[{idx}/{total}] 已存在，跳过：{filename}")
            downloaded_files.append(local_path)
            continue

        log_print(f"[{idx}/{total}] 正在下载：{filename}")
        ok, code, err_msg = _download_with_retries(
            url, local_path, filename, log_print, idx, total
        )
        if ok:
            downloaded_files.append(local_path)
            continue

        if code == 404:
            log_print(f"[{idx}/{total}] 链接失效(404)，尝试刷新：{filename}")
            fresh_url = _resolve_fresh_url_for_filename(
                filename, log_print, stale_url=url, codename=codename)
            if not fresh_url:
                return [], f"下载失败：{filename}，错误：HTTP 404 且无法刷新最新地址（请先在远端执行 apt update 后重试）"

            refreshed_name = _filename_from_url(fresh_url)
            refreshed_path = os.path.join(bundle_dir, refreshed_name)
            log_print(f"[{idx}/{total}] 使用新链接重试：{refreshed_name}")
            ok2, _, err_msg2 = _download_with_retries(
                fresh_url, refreshed_path, refreshed_name, log_print, idx, total
            )
            if not ok2:
                return [], f"下载失败：{filename}，刷新后仍失败：{err_msg2}"

            downloaded_files.append(refreshed_path)
            continue

        return [], err_msg

    return downloaded_files, ""


def upload_bundle(local_files, remote_dir, script_name, log_print):
    # 清理远端目录中历史 deb，避免与本次包集合混装。
    rc, _, err = run_ssh_cmd_rc(
        f"mkdir -p {remote_dir} && rm -f {remote_dir}/*.deb {remote_dir}/{script_name}",
        timeout=30,
    )
    if rc != 0:
        return False, err or "创建远端目录失败"

    total = len(local_files)
    for idx, local_path in enumerate(local_files, start=1):
        name = os.path.basename(local_path)
        log_print(f"上传 [{idx}/{total}]：{name}")
        rc, _, err = run_scp_upload(local_path, f"{remote_dir}/", timeout=300)
        if rc != 0:
            return False, err or f"上传失败：{name}"

    return True, ""


def run_remote_install(remote_dir, sudo_password, script_name):
    cmd = (
        f"cd {remote_dir} && chmod +x {script_name} "
        f"&& sudo -S -k -p '' bash ./{script_name}"
    )
    return run_ssh_cmd_with_input(cmd, sudo_password, timeout=300, force_tty=True)

# ===================== GUI 图形界面 =====================


def main_gui():
    """启动 Tkinter GUI 主界面。

    功能：
        1. 提供 SSH 连接配置输入（别名、离线目录前缀）
        2. 提供 6 个分步骤按钮和 1 个一键全流程按钮
        3. 实时显示操作日志
        4. 支持断点续跑（自动复用已下载的 .deb 和脚本）

    工作流程：
        步骤1: 连接 Ubuntu 并获取系统信息（版本号、架构）
        步骤2: 从 Ubuntu 获取目标包的 .deb 下载链接
        步骤3: 下载所有 .deb 到 Windows 本地
        步骤4: 生成离线安装脚本
        步骤5: 上传 .deb 和脚本到 Ubuntu
        步骤6: 在 Ubuntu 上执行安装脚本
    """
    _ensure_default_config_file()

    # 创建主窗口
    root = tk.Tk()
    root.title(f"Windows → Ubuntu XRDP 离线安装工具 (使用 ssh {SSH_ALIAS} 连接)")
    root.geometry("860x620")

    conn_label = ttk.Label(
        root,
        text=f"当前连接方式：ssh {SSH_ALIAS}",
        font=("微软雅黑", 12, "bold")
    )
    conn_label.pack(pady=10)

    log = scrolledtext.ScrolledText(root, height=20)
    log.pack(fill="both", expand=True, padx=10, pady=5)

    # 初始化默认包名和工作路径
    default_packages = ["xrdp", "lightdm"]
    default_paths = build_artifact_paths(SSH_ALIAS, default_packages)

    # 共享状态字典：记录整个工作流的中间结果，支持分步执行和断点续跑
    state = {
        "codename": "",
        "arch": "",
        "packages": default_packages,
        "include_recommends": False,
        "resolved_packages": [],
        "resolved_include_recommends": False,
        "deb_urls": [],
        "package_names": [],
        "downloaded_files": [],
        "script_path": "",
        "artifact_paths": default_paths,
    }

    package_input_var = tk.StringVar(value="xrdp lightdm")
    include_recommends_var = tk.BooleanVar(value=False)
    ssh_alias_var = tk.StringVar(value=SSH_ALIAS)
    bundle_prefix_var = tk.StringVar(value=BUNDLE_DIR_PREFIX)

    def log_print(text):
        def append():
            log.insert(tk.END, text + "\n")
            log.see(tk.END)
            root.update_idletasks()

        root.after(0, append)

    step_buttons = []

    def set_buttons_state(btn_state):
        def apply_state():
            for button in step_buttons:
                button.config(state=btn_state)

        root.after(0, apply_state)

    def show_info(title, msg):
        root.after(0, lambda: messagebox.showinfo(title, msg))

    def get_artifact_paths():
        return state.get("artifact_paths", default_paths)

    def refresh_artifact_paths(packages):
        state["artifact_paths"] = build_artifact_paths(SSH_ALIAS, packages)
        return state["artifact_paths"]

    def _refresh_conn_display():
        conn_label.config(text=f"当前连接方式：ssh {SSH_ALIAS}")
        root.title(f"Windows → Ubuntu XRDP 离线安装工具 (使用 ssh {SSH_ALIAS} 连接)")

    def apply_runtime_config(save_to_file=False):
        global SSH_ALIAS, BUNDLE_DIR_PREFIX

        new_alias = ssh_alias_var.get().strip()
        new_prefix = bundle_prefix_var.get().strip()
        if not new_alias:
            log_print("❌ SSH_ALIAS 不能为空")
            return False
        if not new_prefix:
            log_print("❌ BUNDLE_DIR_PREFIX 不能为空")
            return False

        SSH_ALIAS = new_alias
        BUNDLE_DIR_PREFIX = new_prefix
        _refresh_conn_display()

        current_packages = parse_package_input(package_input_var.get())
        if not current_packages:
            current_packages = state.get("packages", ["xrdp", "lightdm"])
        state["artifact_paths"] = build_artifact_paths(
            SSH_ALIAS, current_packages)

        if save_to_file:
            ok, result = _save_runtime_config(new_alias, new_prefix)
            if not ok:
                log_print(f"❌ 保存配置失败：{result}")
                return False
            log_print(f"✅ 配置已保存：{result}")
        else:
            log_print("✅ 配置已应用到当前会话")
        return True

    def get_selected_packages():
        packages = parse_package_input(package_input_var.get())
        if not packages:
            log_print("❌ 请输入至少一个待安装包名，例如：xrdp")
            return []
        state["packages"] = packages
        refresh_artifact_paths(packages)
        return packages

    def get_include_recommends():
        value = bool(include_recommends_var.get())
        state["include_recommends"] = value
        return value

    def discover_local_debs(expected_names=None):
        bundle_dir = os.path.join(
            os.getcwd(), get_artifact_paths()["bundle_dir"])
        if not os.path.isdir(bundle_dir):
            return []
        expected_identity_set = _deb_identity_set_from_filenames(
            expected_names or [])
        files = []
        for name in os.listdir(bundle_dir):
            if name.endswith(".deb"):
                if expected_identity_set:
                    identity = _deb_identity_from_filename(name)
                    if identity not in expected_identity_set:
                        continue
                files.append(os.path.join(bundle_dir, name))
        files.sort()
        return files

    def run_step(task):
        def wrapped():
            set_buttons_state("disabled")
            try:
                task()
            finally:
                set_buttons_state("normal")

        threading.Thread(target=wrapped, daemon=True).start()

    def step_connect_info():
        """[步骤 1] 通过 SSH 连接到远端 Ubuntu，获取系统版本和架构信息。"""
        log_print("[步骤 1/6] 正在连接 Ubuntu 获取系统版本...")
        codename, arch, info_err = get_ubuntu_info()
        if not codename:
            log_print(f"❌ 连接失败，请确认 ssh {SSH_ALIAS} 能正常登录")
            if info_err:
                log_print(f"⚠️ 详细错误：{info_err}")
            return

        state["codename"] = codename
        state["arch"] = arch
        log_print(f"✅ 系统版本：{codename}  架构：{arch}")

    def step_get_links():
        """[步骤 2] 通过远端 apt-get 解析目标包及其依赖，获取 .deb 下载直链。"""
        if not state["codename"]:
            step_connect_info()
            if not state["codename"]:
                return

        packages = get_selected_packages()
        if not packages:
            return
        include_recommends = get_include_recommends()

        log_print("[步骤 2/6] 正在获取 .deb 直链列表...")
        log_print(f"目标包：{' '.join(packages)}")
        log_print(
            f"依赖策略：{'包含推荐包' if include_recommends else '仅必需依赖（不含推荐包）'}")
        deb_urls, pkg_err = get_deb_urls(
            packages,
            include_recommends=include_recommends,
            codename=state.get("codename", ""),
        )
        if not deb_urls:
            log_print("❌ 获取依赖失败")
            if pkg_err:
                log_print(f"⚠️ 详细错误：{pkg_err}")
            return

        if pkg_err:
            log_print(f"⚠️ 解析告警：{pkg_err}")

        state["deb_urls"] = deb_urls
        state["resolved_packages"] = packages.copy()
        state["resolved_include_recommends"] = include_recommends
        state["package_names"] = make_package_names(deb_urls)
        version_file, package_file, links_file = save_files(
            state["codename"],
            state["arch"],
            state["package_names"],
            deb_urls,
            get_artifact_paths(),
        )
        log_print(f"✅ 共获取到 {len(deb_urls)} 个 .deb 直链")
        log_print(f"✅ 已更新 {links_file} / {package_file}")
        log_print(f"ℹ️ 系统信息文件：{version_file}")

    def step_download_debs():
        """[步骤 3] 从互联网下载所有 .deb 文件到本地 Windows 目录。

        说明：
            - 支持断点续下：已存在的 .deb 文件会跳过
            - 若链接 404，会自动从镜像目录扫描刷新到新版本
            - 最多重试 4 次，间隔时间指数退避
        """
        packages = get_selected_packages()
        if not packages:
            return
        include_recommends = get_include_recommends()

        resolved_packages = state.get("resolved_packages", [])
        resolved_include_recommends = state.get(
            "resolved_include_recommends", False)
        if (
            not resolved_packages
            or set(packages) != set(resolved_packages)
            or include_recommends != resolved_include_recommends
        ):
            log_print("❌ 当前包名或推荐包开关与已解析依赖不一致，请先执行“步骤2：获取直链与清单”")
            return

        deb_urls = state["deb_urls"]
        if not deb_urls:
            log_print("❌ 当前会话未缓存依赖链接，请先执行“步骤2：获取直链与清单”")
            return

        if not deb_urls:
            log_print("❌ 未找到可下载链接，请先执行“步骤2：获取直链”")
            return

        log_print("[步骤 3/6] 正在下载所有 .deb 到本地...")
        bundle_dir = os.path.join(
            os.getcwd(), get_artifact_paths()["bundle_dir"])
        downloaded_files, download_err = download_debs(
            deb_urls, bundle_dir, log_print, codename=state.get("codename", ""))
        if not downloaded_files:
            log_print("❌ 下载失败")
            if download_err:
                log_print(f"⚠️ 详细错误：{download_err}")
            return

        state["downloaded_files"] = downloaded_files
        log_print(f"✅ 下载完成，共 {len(downloaded_files)} 个文件")

    def step_build_script():
        """[步骤 4] 根据目标包生成自适应的离线安装脚本。

        脚本功能：
            - 双重 dpkg 安装 + 配置修复
            - XRDP 升级中断恢复
            - 自动启用相关服务
            - 防火墙规则配置（XRDP 3389 端口）
        """
        packages = get_selected_packages()
        if not packages:
            return

        log_print("[步骤 4/6] 正在生成安装脚本...")
        paths = get_artifact_paths()
        bundle_dir = os.path.join(os.getcwd(), paths["bundle_dir"])
        state["script_path"] = build_install_script(
            bundle_dir,
            packages,
            paths["script_name"],
        )
        log_print(f"目标包：{' '.join(packages)}")
        log_print(f"✅ 安装脚本已生成：{state['script_path']}")

    def step_upload_bundle():
        """[步骤 5] 通过 SCP 将本地 .deb 和安装脚本上传到远端 Ubuntu。

        说明：
            - 会清理远端目录中历史 .deb，避免混装
            - 支持中途中断后重新上传（已存在则覆盖）
            - 超时时间较长（最多 300 秒），适应大文件传输
        """
        expected_names = state.get("package_names", [])
        packages = get_selected_packages()
        if not packages:
            return
        include_recommends = get_include_recommends()

        resolved_packages = state.get("resolved_packages", [])
        resolved_include_recommends = state.get(
            "resolved_include_recommends", False)
        if (
            not resolved_packages
            or set(packages) != set(resolved_packages)
            or include_recommends != resolved_include_recommends
        ):
            log_print("❌ 当前包名或推荐包开关与已解析依赖不一致，请先执行“步骤2：获取直链与清单”")
            return

        files = state["downloaded_files"]
        if not files:
            files = discover_local_debs(expected_names=expected_names)
            if files:
                state["downloaded_files"] = files

        if not files:
            if expected_names:
                log_print("❌ 未找到当前目标包对应的本地 .deb，请先执行“步骤3：下载 .deb”")
            else:
                log_print("❌ 未找到当前依赖清单，请先执行“步骤2/3”生成并下载当前包依赖")
            return

        if expected_names:
            expected_identity_set = _deb_identity_set_from_filenames(
                expected_names)
            files = [
                f for f in files
                if _deb_identity_from_filename(os.path.basename(f)) in expected_identity_set
            ]
            state["downloaded_files"] = files
            if not files:
                log_print("❌ 本地目录有 .deb，但都不属于当前输入包的依赖集合")
                log_print("💡 请先执行“步骤2 获取直链与清单”后再执行“步骤3 下载 .deb”")
                return
            actual_identity_set = {
                _deb_identity_from_filename(os.path.basename(f)) for f in files
            }
            if len(actual_identity_set) != len(expected_identity_set):
                log_print(
                    f"❌ 本地依赖不完整：需要 {len(expected_identity_set)} 个，当前仅 {len(actual_identity_set)} 个")
                log_print("💡 请重新执行“步骤3 下载 .deb”补齐后再上传")
                return
            log_print(f"ℹ️ 本次仅上传当前目标包对应的 {len(files)} 个 .deb")

        script_path = state["script_path"]
        if not script_path or not os.path.exists(script_path):
            packages = get_selected_packages()
            if not packages:
                return
            paths = get_artifact_paths()
            bundle_dir = os.path.join(os.getcwd(), paths["bundle_dir"])
            script_path = build_install_script(
                bundle_dir,
                packages,
                paths["script_name"],
            )
            state["script_path"] = script_path
            log_print("ℹ️ 未找到安装脚本，已自动重新生成")

        paths = get_artifact_paths()
        remote_dir = paths["remote_dir"]
        script_name = paths["script_name"]
        log_print(f"[步骤 5/6] 正在上传 .deb + {script_name} 到 Ubuntu...")
        all_upload_files = files + [script_path]
        ok, upload_err = upload_bundle(
            all_upload_files,
            remote_dir,
            script_name,
            log_print,
        )
        if not ok:
            log_print("❌ 上传失败")
            if upload_err:
                log_print(f"⚠️ 详细错误：{upload_err}")
            return
        log_print("✅ 上传完成")

    def step_remote_install():
        """[步骤 6] 在远端 Ubuntu 上执行离线安装脚本。

        过程：
            1. 校验 sudo 密码
            2. 在远端执行安装脚本
            3. 显示执行结果和后续检查建议

        说明：
            - 需要提供 Ubuntu 用户的 sudo 密码
            - 脚本权限自动设置为可执行
        """
        sudo_password = state.get("sudo_password", "")
        if not sudo_password:
            log_print("❌ 未提供 sudo 密码，已取消远端安装")
            return

        log_print("正在校验 sudo 密码...")
        valid, _, _, verify_err = verify_sudo_password(sudo_password)
        if not valid:
            log_print("❌ sudo 密码校验失败，请重新输入后再试")
            if verify_err:
                log_print("---- sudo 校验错误 ----")
                log_print(redact_secrets(verify_err, [sudo_password]))
            return

        log_print("[步骤 6/6] 正在 Ubuntu 上执行安装脚本...")
        paths = get_artifact_paths()
        remote_dir = paths["remote_dir"]
        script_name = paths["script_name"]
        rc, out, err = run_remote_install(
            remote_dir, sudo_password, script_name)
        safe_out = redact_secrets(out, [sudo_password])
        safe_err = redact_secrets(err, [sudo_password])
        if rc != 0:
            log_print("❌ 远端安装失败")
            if safe_out:
                log_print("---- 远端输出 ----")
                log_print(safe_out)
            if safe_err:
                log_print("---- 远端错误 ----")
                log_print(safe_err)
            log_print("💡 若提示 sudo 需要密码，请在终端手动执行：")
            log_print(
                f"ssh -t {SSH_ALIAS} \"cd {remote_dir} && sudo bash ./{script_name}\"")
            return

        if safe_out:
            log_print("---- 远端输出 ----")
            log_print(safe_out)

        log_print("\n🎉 全部完成！")
        log_print("📌 建议后续检查：")
        for hint in make_post_install_hints(state.get("packages", [])):
            log_print(f"  - {hint}")
        log_print("📁 生成文件：")
        paths = get_artifact_paths()
        log_print(f"  → {paths['version_file']}")
        log_print(f"  → {paths['package_file']}")
        log_print(f"  → {paths['links_file']}")
        log_print(
            f"  → {paths['bundle_dir']}/ (*.deb + {paths['script_name']})")
        show_info("成功", "离线包下载、上传、远端安装已完成！")

    def workflow_all():
        step_connect_info()
        if not state["codename"]:
            return
        step_get_links()
        if not state["deb_urls"]:
            return
        step_download_debs()
        if not state["downloaded_files"]:
            return
        step_build_script()
        step_upload_bundle()
        step_remote_install()

    def ask_sudo_password():
        pwd = simpledialog.askstring(
            "sudo 密码",
            f"请输入 Ubuntu 用户在 {SSH_ALIAS} 上的 sudo 密码：",
            parent=root,
            show="*",
        )
        return pwd

    def run_step_with_password(task):
        pwd = ask_sudo_password()
        if not pwd:
            log_print("ℹ️ 已取消：未输入 sudo 密码")
            return

        state["sudo_password"] = pwd
        run_step(task)

    btn_frame = ttk.Frame(root)
    btn_frame.pack(fill="x", padx=10, pady=8)

    # ========== 配置框：SSH 别名和离线目录前缀 ==========
    config_frame = ttk.Frame(root)
    config_frame.pack(fill="x", padx=10, pady=(2, 8))
    ttk.Label(config_frame, text="SSH 别名：").pack(side="left")
    ttk.Entry(config_frame, textvariable=ssh_alias_var, width=14).pack(
        side="left", padx=(4, 10))
    ttk.Label(config_frame, text="离线目录前缀：").pack(side="left")
    ttk.Entry(config_frame, textvariable=bundle_prefix_var, width=20).pack(
        side="left", padx=(4, 10))
    ttk.Button(
        config_frame,
        text="应用并保存配置",
        command=lambda: apply_runtime_config(save_to_file=True),
    ).pack(side="left")

    # ========== 包管理框：输入待安装包名和依赖策略 ==========
    pkg_frame = ttk.Frame(root)
    pkg_frame.pack(fill="x", padx=10, pady=(2, 8))
    ttk.Label(pkg_frame, text="待安装包名：").pack(side="left")
    pkg_entry = ttk.Entry(pkg_frame, textvariable=package_input_var)
    pkg_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
    recommend_chk = ttk.Checkbutton(
        pkg_frame,
        text="包含推荐包",
        variable=include_recommends_var,
    )
    recommend_chk.pack(side="left", padx=(8, 0))
    ttk.Label(
        pkg_frame, text="（包名可空格/逗号分隔）").pack(side="left", padx=(8, 0))

    btn1 = ttk.Button(btn_frame, text="步骤1 连接并读取系统",
                      command=lambda: run_step(step_connect_info))
    btn2 = ttk.Button(btn_frame, text="步骤2 获取直链与清单",
                      command=lambda: run_step(step_get_links))
    btn3 = ttk.Button(btn_frame, text="步骤3 下载 .deb",
                      command=lambda: run_step(step_download_debs))
    btn4 = ttk.Button(btn_frame, text="步骤4 生成安装脚本",
                      command=lambda: run_step(step_build_script))
    btn5 = ttk.Button(btn_frame, text="步骤5 上传到 Ubuntu",
                      command=lambda: run_step(step_upload_bundle))
    btn6 = ttk.Button(btn_frame, text="步骤6 远端执行安装",
                      command=lambda: run_step_with_password(step_remote_install))
    btn_all = ttk.Button(btn_frame, text="一键全流程",
                         command=lambda: run_step_with_password(workflow_all))

    for i, button in enumerate([btn1, btn2, btn3, btn4, btn5, btn6, btn_all]):
        button.grid(row=0 if i < 4 else 1, column=i if i <
                    4 else i - 4, padx=4, pady=4, sticky="ew")

    for c in range(4):
        btn_frame.columnconfigure(c, weight=1)

    step_buttons.extend([btn1, btn2, btn3, btn4, btn5, btn6, btn_all])

    log_print("已启用分步按钮：可按步骤执行，也可点“一键全流程”。")
    log_print("断点续跑提示：步骤3/5会自动复用已下载的 .deb 与本地脚本。")
    root.mainloop()


if __name__ == "__main__":
    main_gui()
