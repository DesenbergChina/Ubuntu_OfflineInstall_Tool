# Windows -> Ubuntu XRDP Offline Install Tool

English version: [docs/README_EN.md](docs/README_EN.md)

## 项目简介

这是一个面向 Windows 运维侧的 Ubuntu 离线安装工具（Tkinter GUI）。
它通过 SSH 连接远端 Ubuntu，解析目标包及依赖的 .deb 下载链接，在 Windows 本地完成下载，再上传回 Ubuntu 执行离线安装脚本。

当前仓库主要围绕 XRDP 离线部署与修复场景进行优化，尤其针对以下高频问题做了加固：

- 依赖链复杂导致的安装中断
- 镜像 404 后链接失效
- 跨发行版包混入导致 ABI/依赖不兼容
- 已下载文件复用与断点续跑

## 适用场景

- 远端 Ubuntu 服务器无法直接联网安装软件
- 需要在 Windows 上集中准备离线安装包
- 需要批量或重复执行 XRDP 及相关组件部署

## 主要功能

- 通过 SSH 自动识别远端 Ubuntu 发行版代号与架构
- 使用 apt-get --print-uris 获取目标包及依赖直链
- 自动下载 .deb，支持跳过已存在文件
- 下载失败时自动重试，404 时支持镜像目录刷新兜底
- 严格过滤跨发行版包，降低混装风险
- 自动生成 install_*.sh 离线安装脚本
- SCP 上传离线包并远端执行安装
- 支持分步执行和一键全流程
- 支持断点续跑（复用已下载 .deb 与本地脚本）

## 目录说明

- release/ubuntu_OfflineInstall_Tool_release.py: 发布版 GUI 主程序（推荐入口）
- release/xrdp_offline_config_release.json: 发布版配置文

## 运行环境

- Windows（已安装 Python 3.6+）
- 可用的 ssh/scp 命令（建议安装并配置 OpenSSH 客户端）
- 远端 Ubuntu 主机可通过 SSH 别名访问

## 配置说明

发布版默认读取 release/xrdp_offline_config_release.json：

- SSH_ALIAS: SSH 配置中的主机别名
- BUNDLE_DIR_PREFIX: 本地离线目录前缀（最终目录名会带目标标识）

示例：

{
  "SSH_ALIAS": "test",
  "BUNDLE_DIR_PREFIX": "offline_bundle"
}

建议先在终端验证：

- ssh <SSH_ALIAS>
- scp 任意测试文件到远端

## 使用方法

### 1. 启动 GUI

在仓库根目录运行：
```
python release/ubuntu_OfflineInstall_Tool_release.py
```
### 2. 在界面中设置参数

- SSH 别名
- 离线目录前缀
- 待安装包名（支持空格或逗号分隔）
- 是否包含推荐包

### 3. 按步骤执行（推荐顺序）

- 步骤1 连接并读取系统
- 步骤2 获取直链与清单
- 步骤3 下载 .deb
- 步骤4 生成安装脚本
- 步骤5 上传到 Ubuntu
- 步骤6 远端执行安装（会提示输入 sudo 密码）

也可以直接使用“一键全流程”。

### 4. 产物与结果

执行后会在仓库目录生成/更新：

- ubuntu_version_*.txt: 远端系统版本与架构信息
- package_list_*.txt: 解析得到的依赖包文件名列表
- download_links_*.txt: 依赖包直链列表
- offline_bundle_*/: 下载的 .deb 与 install_*.sh

## 常见问题

- 步骤2失败：优先检查 SSH 连通性、远端 apt 源是否可用、包名是否正确。
- 步骤3出现 404：工具会自动尝试刷新链接；若仍失败，建议更换可用镜像源后重试。
- 远端安装失败：检查 sudo 密码、磁盘空间、dpkg 状态；必要时执行 dpkg --configure -a 修复。
- XRDP 蓝屏：优先先安装核心修复集，确认核心链路正常后再安装 XFCE 相关增强包。

## 开源许可证

本项目已采用 Apache License 2.0（Apache-2.0）。

- 完整许可证文本见仓库根目录 LICENSE 文件
- 项目声明信息见仓库根目录 NOTICE 文件

你在使用本项目代码时，通常需要遵循以下规则：

1. 保留版权声明与许可证文本
2. 对修改过的文件给出修改说明
3. 分发时附带 LICENSE 与 NOTICE
4. 不得使用作者或贡献者名称进行背书（除非获得书面许可）

说明：Apache-2.0 允许商用和闭源集成，并提供明确的专利授权条款。

## 致谢

感谢 Ubuntu、OpenSSH、XRDP 及相关开源社区生态。
