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

如果不确定 Windows 是否已经安装 OpenSSH，请在 PowerShell 中执行：

```powershell
ssh -V
scp -V
```

能够显示版本号即可。若提示找不到命令，需要先在 Windows 的“可选功能”中安装
“OpenSSH 客户端”。

## 配置说明

发布版默认读取 release/xrdp_offline_config_release.json：

- SSH_ALIAS: SSH 配置中的主机别名，也就是在 `ssh` 命令后面使用的短名称
- BUNDLE_DIR_PREFIX: 本地离线目录前缀（最终目录名会带目标标识）

### SSH 别名是什么

SSH 别名不是 Ubuntu 的用户名，也不是密码；它是给一台远程服务器起的一个短名称。
工具会根据这个名称执行类似下面的命令：

```powershell
ssh test
scp 本地文件 test:/tmp/
```

例如，远程服务器的实际信息可能是：

- IP 地址：`192.168.1.100`
- SSH 用户名：`ubuntu`
- SSH 端口：`22`

我们可以把它命名为 `test`。在 Windows 用户目录下创建或编辑 SSH 配置文件：

```text
C:\Users\你的Windows用户名\.ssh\config
```

加入以下内容（请将 IP 和用户名替换成实际值）：

```sshconfig
Host test
    HostName 192.168.1.100
    User ubuntu
    Port 22
```

这里的 `Host test` 就是本工具要填写的 SSH 别名。配置完成后，先在 PowerShell
中测试：

```powershell
ssh test
```

如果能登录到 Ubuntu，再在工具窗口的“SSH 别名”输入框中填写 `test`。使用 SSH
密钥时，可以在上面的配置中继续添加 `IdentityFile`，例如：

```sshconfig
    IdentityFile C:\Users\你的Windows用户名\.ssh\id_ed25519
```

如果使用密码登录，密码不会写入 README、JSON 或 SSH 配置文件；根据提示输入即可。

> 注意：`Host test`、GUI 中的“SSH 别名”以及命令 `ssh test` 中的 `test` 必须完全一致。
> 别名可以改成其他名称，例如 `ubuntu-server`，但三处都要同步修改。

### 配置文件示例

{
  "SSH_ALIAS": "test",
  "BUNDLE_DIR_PREFIX": "offline_bundle"
}

上面的 JSON 文件位于 `release/xrdp_offline_config_release.json`。修改后重新启动程序，
或者直接在 GUI 中修改“SSH 别名”和“离线目录前缀”，点击“应用并保存配置”。

建议先在终端验证 SSH 和文件上传：

```powershell
ssh test
scp .\README.md test:/tmp/
```

两条命令都成功后，再运行安装工具。

## 使用方法

### 1. 启动 GUI

在仓库根目录运行：
```
python release/ubuntu_OfflineInstall_Tool_release.py
```

### 2. 填写界面参数

截图中的窗口从上到下可以这样填写：

- **SSH 别名**：填写已经在 `config` 文件中配置并通过 `ssh test` 测试成功的名称，例如 `test`。
- **离线目录前缀**：填写本地工作目录的前缀，通常保持 `offline_bundle` 即可。
- **待安装包名**：填写要安装的软件包，例如 `xrdp lightdm`；多个包可以用空格或逗号分隔。
- **包含推荐包**：建议勾选。勾选后会同时获取 APT 推荐依赖，通常更有利于完整使用桌面和 XRDP 功能。

填写完成后点击“应用并保存配置”，以后启动程序时可以继续使用这些值。

### 3. 按步骤执行（推荐新手使用）

建议按照截图中的按钮从左到右、从上到下执行：

1. **步骤1 连接并读取系统**：测试 SSH 连接，并读取 Ubuntu 版本、发行版代号和 CPU 架构。
2. **步骤2 获取直链与清单**：根据远端 Ubuntu 版本解析 `xrdp`、`lightdm` 及其依赖的 `.deb` 下载地址。
3. **步骤3 下载 .deb**：在 Windows 本地下载软件包。已经下载过且文件完整的包会复用，不必重复下载。
4. **步骤4 生成安装脚本**：在离线目录中生成 `install_*.sh`。
5. **步骤5 上传到 Ubuntu**：使用 `scp` 将 `.deb` 文件和安装脚本上传到远端。
6. **步骤6 远端执行安装**：在 Ubuntu 上执行安装脚本。此时通常会提示输入 Ubuntu 用户的 `sudo` 密码。

如果以上准备都已完成，也可以点击 **一键全流程**，工具会按相同顺序自动执行。
首次使用建议分步执行，这样更容易定位是 SSH、下载还是远端安装环节出现问题。

### 4. 断点续跑

中途失败后不必立刻删除目录。重新打开工具，确认“SSH 别名”和待安装包名一致，
再从失败的步骤继续执行即可。工具会复用已经下载的 `.deb` 和已生成的脚本。

### 5. 产物与结果

执行后会在仓库目录生成/更新：

- ubuntu_version_*.txt: 远端系统版本与架构信息
- package_list_*.txt: 解析得到的依赖包文件名列表
- download_links_*.txt: 依赖包直链列表
- offline_bundle_*/: 下载的 .deb 与 install_*.sh

**因此我推荐将脚本放在一个文件夹下，避免出现的文件找不到**

## 常见问题

- 步骤2失败：优先检查 SSH 连通性、远端 apt 源是否可用、包名是否正确。
- 步骤3出现 404：工具会自动尝试刷新链接；若仍失败，建议更换可用镜像源后重试。
- 远端安装失败：检查 sudo 密码、磁盘空间、dpkg 状态；必要时执行 dpkg --configure -a 修复。
- 安装之后功能不正常：可能是没有勾选包含推荐包

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
