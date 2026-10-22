# Downloadminercaft

一个用 Python + [ttkbootstrap](https://github.com/israel-dryer/ttkbootstrap) 写的 Minecraft 下载器，带图形界面，支持**客户端 / 服务端 / 下载加载器 / 整合包 / 安装加载器**五个工作区。

仓库地址：<https://github.com/DawnEcho1254/Downloadminercaft>

## 功能

- **客户端**：浏览 Mojang 正式版 / 预览版 / 愚人节版 / 早期版本，支持 Mojang 官方与 BMCLAPI 镜像切换、失败回退；下载原版 `jar` 时会在同目录补一份可被 PCL / HMCL 识别的版本 JSON。
- **服务端**：原版服务端，以及 Paper、Purpur、Leaves、Folia 的版本列表与构建下载。
- **加载器**：Fabric、Forge、NeoForge 的版本浏览和安装器下载，自动处理官方源 / 镜像、Forge promos 与 NeoForge 版本号映射。
- **整合包**：拖入或选择 `.mrpack` / 含 `mods`、`config` 的 `.zip`，自动校验格式并列出全部 Mod 与配置文件；按标准目录结构安装，只写入整合包实际覆盖的配置；生成自包含的版本 JSON（已合并加载器主类与依赖，不再是子版本）、按整合包名命名的原版 `jar` 和 `整合包信息.txt`。
- **安装加载器**：选择 `.minecraft` 文件夹，扫描 `versions` 下同时含 JAR 与 JSON 的版本；读取版本 JSON 判断游戏版本，向选中版本直接注入 Fabric / Forge / NeoForge（合并为完整版本并自动备份原 JSON），已装加载器的版本会禁止重复安装。
- 明暗主题切换、版本列表刷新、多线程下载、单文件进度与总进度、下载目录文件树。

## 运行

需要 Python 3.10+（Windows）。

```bash
pip install ttkbootstrap
python Downloadminercaft.py
```

图标 `mc_downloader.ico` 是可选的：放在脚本同目录时会自动作为窗口图标。

---

## Mojang EULA（最终用户许可协议）

下载或使用 Minecraft 即表示你同意 Mojang 的最终用户许可协议。官方入口：

- **EULA 主页面（英文）**：https://www.minecraft.net/en-us/eula
- **EULA 主页面（简体中文）**：https://www.minecraft.net/zh-hans/eula
- **快捷短链**：https://aka.ms/MinecraftEULA
- **旧版 Mojang 账户条款存档**：https://account.mojang.com/terms

请在使用本工具前阅读并遵守 EULA 条款，尤其是关于**分发、商业用途和修改客户端**的部分。

---

## 编写说明

- **代码由 AI（DeepSeek）编写**。需求梳理、功能迭代与脚本实现均在与 AI 的对话中完成，仓库维护者负责整理与发布。
- **本 README 由 AI（DeepSeek）编写**。

## 鸣谢

本项目能跑起来，靠的是这些服务和项目：

- **Mojang Studios / Microsoft**：Minecraft 本体、官方版本清单与资源 API。
- **BMCLAPI（bangbang93）**：国内镜像，包括版本清单、Forge / NeoForge Maven 与 Fabric meta 镜像。
- **ttkbootstrap、Tcl/Tk**：图形界面、主题与控件。
- **FabricMC、MinecraftForge、NeoForged、QuiltMC**：加载器、安装器与版本 profile。
- **PaperMC、PurpurMC、LeavesMC**：服务端版本与构建 API。
- **Modrinth**：`.mrpack` 整合包规范与文件 CDN。
- **PCL2、HMCL**：版本隔离目录结构的参考。
- **DeepSeek**：代码与 README 的编写。

## 开源协议

本项目采用 **MIT License**，完整条款见仓库根目录的 [`LICENSE`](LICENSE)。

## 许可与免责

本工具仅提供 Minecraft 相关文件的下载便利，**不包含任何游戏资源本体**，所有文件均来自 Mojang 官方、BMCLAPI 镜像、各加载器官方 Maven 及第三方服务端官方 API。使用前请确保你已拥有正版 Minecraft。

- **MIT 许可**：可自由使用、修改、分发乃至商用，但必须保留 `LICENSE` 中的版权声明与许可文本。
- **署名**：分发或修改后的版本请保留版权声明与许可文本，建议注明本仓库地址。
- **无担保**：本工具按“现状”提供，不保证可用性、稳定性或下载内容的完整性。
- **责任限制**：因使用或无法使用本工具造成的任何直接或间接损失（包括但不限于账号封禁、数据丢失、设备损坏、法律纠纷），作者及贡献者**不承担任何责任**。使用风险由使用者自行承担。
- **版权**：Minecraft 及相关素材的版权归 Mojang Studios / Microsoft 所有。若相关方认为本项目存在侵权，请联系仓库维护者，我们会及时处理。

UA 字段中的联系方式为本仓库链接。
